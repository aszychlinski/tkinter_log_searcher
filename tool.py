from queue import Empty, Queue
from parsel import Selector  # pip install parsel
from pathlib import Path
from requests import get  # pip install requests
from requests.exceptions import ConnectionError
import threading as th
from time import sleep
import tkinter as tk
import tkinter.ttk as ttk
from typing import List, Optional
from webbrowser import open_new_tab

SERVERS = ['http://log.server1.company.com/?location=/logs', 'http://log.server2.company.com/?location=/logs']


class InputArea(tk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master=master, **kwargs)
        self.merchant_label = tk.Label(self, text='Merchant ID: ')
        self.merchant_entry = tk.Entry(self)
        self.query_label = tk.Label(self, text='Query: ')
        self.query_entry = tk.Entry(self)
        self.search_button = tk.Button(self, text='Search', bg='green2', command=self.search)
        self.default_colour = self.search_button
        self.on_top_boolean = tk.BooleanVar()
        self.on_top_checkbutton = tk.Checkbutton(
            self,
            text='Always on top?',
            variable=self.on_top_boolean,
            command=lambda: self.master.attributes('-topmost', self.on_top_boolean.get())
        )
        self.on_top_checkbutton.pack(side='right', pady=2)
        self.info_label = tk.Label(
            self,
            text='Left-click a result button to open in browser, right-click to copy link to clipboard.',
            bg='yellow'
        )
        for w in [self.merchant_label, self.merchant_entry, self.query_label, self.query_entry, self.search_button,
                  self.info_label]:
            w.pack(side='left', pady=2, padx=2)
        self.merchant_entry.focus()

    def search(self):
        if not self.check_health():
            return
        for column in self.master.columns:
            column.start()
        self.search_button.config(state='disabled', bg='SystemButtonFace')
        self.master.after(1000, self.check_for_completion)

    def check_for_completion(self):
        if any(column.outer_thread.any_child_alive for column in self.master.columns):
            self.master.after(1000, self.check_for_completion)
        else:
            self.search_button.config(state='normal', bg='green2')

    def check_health(self) -> bool:
        code_colours = {1: 'yellow', 2: 'green2', 3: 'yellow', 4: 'red', 5: 'red'}
        result = True
        for column in self.master.columns:
            try:
                response_code = get(column.server).status_code
            except ConnectionError:
                column.health_display.config(bg='red')
                column.health_var.set('VPN')
                result = False
            else:
                column.health_display.config(bg=code_colours[response_code // 100])
                column.health_var.set(response_code)
        return result


class ResultArea(tk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master=master, **kwargs)
        for server in SERVERS:
            new_column = ResultColumn(self, server, SERVERS.index(server) + 1)
            self.master.columns.append(new_column)
            new_column.pack(side='left', expand=True, fill='both')


class ResultButton(tk.Button):
    def __init__(self, master, url, result_no, hits, source, filename, **kwargs):
        self.url = url
        super().__init__(master=master,
                         text=f'#{int(result_no):04} | Hits: {int(hits):04} | Source: {source} | {filename}',
                         anchor='w',
                         command=self.open_and_sink,
                         **kwargs)
        self.pack(side='top', fill='x')
        self.bind('<Button-3>', self.copy_url_to_clipboard)

    def open_and_sink(self):
        open_new_tab(self.url)
        self.config(relief=tk.SUNKEN)

    def copy_url_to_clipboard(self, _):
        self.clipboard_clear()
        self.clipboard_append(self.url)


class InnerThread(th.Thread):
    def __init__(self, queue, column):
        super().__init__()
        self.stop_signal = False
        self.queue = queue
        self.column: ResultColumn = column
        self.query = column.query
        self.cache_counter: ThreadSafeCounter = column.cache_counter
        self.request_counter: ThreadSafeCounter = column.request_counter

    def run(self):
        while not self.stop_signal:
            try:
                folder_href = self.queue.get(block=False)
            except Empty:
                self.stop_signal = True
                continue
            filename = folder_href.split('/')[-1]
            if Path(fr'.\log_cache\{self.column.merchant_id}\{filename}').is_file():
                with open(fr'.\log_cache\{self.column.merchant_id}\{filename}', mode='r', encoding='utf-8') as file:
                    data = file.read()
                source = ' CACHE'
                self.cache_counter.increment()
            else:
                data = get(folder_href).text.split('<pre>')[1].split('</pre>')[0]
                with open(fr'.\log_cache\{self.column.merchant_id}\{filename}', mode='x', encoding='utf-8') as file:
                    file.write(data)
                source = '   WEB   '
                self.request_counter.increment()
            if self.query in data:
                with self.column.master.master.button_creation_lock:
                    self.column.result_counter.increment()
                    self.column.result_buttons.append(
                        ResultButton(master=self.column.canvas_frame,
                                     url=folder_href,
                                     result_no=self.column.result_counter.get(),
                                     hits=data.count(self.query),
                                     source=source,
                                     filename=filename,
                                     width=180)
                    )
                    self.column.canvas.configure(scrollregion=self.column.canvas.bbox('all'))
        sleep(1)
        self.column.canvas.configure(scrollregion=self.column.canvas.bbox('all'))


class OuterThread(th.Thread):
    def __init__(self, master, **kwargs):
        super().__init__(**kwargs)
        self.master: ResultColumn = master
        self.queue = Queue()
        self.request_threads: List[InnerThread] = []

    def run(self):
        merchant_folders = Selector(
            text=get(self.master.server).text
        ).xpath('//li/a[contains(@href, "session-logs")]/text()').getall()
        if self.master.merchant_id + '/' in merchant_folders:
            log_folders = get(f'{self.master.server}/{self.master.merchant_id}').text
            selector = Selector(text=log_folders).xpath('//a[text()="download"]/preceding-sibling::a/@href').getall()
            server_root = self.master.server.split('?')[0]
            Path(fr'.\log_cache\{self.master.merchant_id}').mkdir(parents=True, exist_ok=True)
            for item in selector:
                self.queue.put(server_root + item)
            for _ in range(5):
                self.request_threads.append(InnerThread(self.queue, self.master))
            for thread in self.request_threads:
                thread.start()
        else:
            raise RuntimeError('Merchant Folder not found')

    @property
    def any_child_alive(self) -> bool:
        return any(thread.is_alive() for thread in self.request_threads)


class ThreadSafeCounter(tk.IntVar):  # https://julien.danjou.info/atomic-lock-free-counters-in-python/
    def __init__(self):
        super().__init__()
        self._lock = th.Lock()

    def increment(self):
        with self._lock:
            self.set(self.get() + 1)


class ResultColumn(tk.LabelFrame):
    def __init__(self, master, server, _id, **kwargs):
        super().__init__(master, **kwargs)
        self.id = _id
        self.outer_thread: Optional[OuterThread] = None
        self.server = server
        self.result_buttons: List[ResultButton] = []
        self.result_counter = ThreadSafeCounter()
        self.cache_counter, self.request_counter = ThreadSafeCounter(), ThreadSafeCounter()
        self.health_var = tk.StringVar(value='       ')
        self.info_frame = tk.Frame(self)
        self.server_label = tk.Label(self.info_frame, text='Server: ')
        self.server_label.pack(side='left', pady=2)
        self.server_button = tk.Button(self.info_frame, text=server.split('?')[0], command=lambda: open_new_tab(server))
        self.server_button.pack(side='left', pady=2)
        self.health_label = tk.Label(self.info_frame, text='Health: ')
        self.health_label.pack(side='left', pady=2)
        self.health_display = tk.Label(self.info_frame, textvariable=self.health_var, relief=tk.SUNKEN)
        self.health_display.pack(side='left', pady=2, ipadx=10)
        self.cache_label = tk.Label(self.info_frame, text='Cache reads: ')
        self.cache_label.pack(side='left', pady=2)
        self.cache_display = tk.Label(self.info_frame, textvariable=self.cache_counter, relief=tk.SUNKEN)
        self.cache_display.pack(side='left', pady=2, ipadx=10)
        self.request_label = tk.Label(self.info_frame, text='Requests made: ')
        self.request_label.pack(side='left', pady=2)
        self.request_display = tk.Label(self.info_frame, textvariable=self.request_counter, relief=tk.SUNKEN)
        self.request_display.pack(side='left', pady=2, ipadx=10)
        self.info_frame.pack(side='top')
        self.stop_button = tk.Button(self, text='STOP', command=self.stop, bg='orange')
        self.stop_button.pack(pady=2, fill='x')
        ttk.Separator(self).pack(side='top', expand=False, fill='x')
        self.canvas = tk.Canvas(self, bg='lawn green')
        self.canvas_frame = tk.Frame(self.canvas)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(pady=2, expand=True, fill='both')
        self.canvas.pack_propagate(0)
        self.canvas.create_window((0, 0), window=self.canvas_frame, anchor='nw')

    @property
    def merchant_id(self) -> str:
        return self.master.master.input_area.merchant_entry.get()

    @property
    def query(self) -> str:
        return self.master.master.input_area.query_entry.get()

    def stop(self):
        for request_thread in self.outer_thread.request_threads:
            request_thread.stop_signal = True

    def start(self):
        if self.outer_thread:
            while self.outer_thread.any_child_alive:
                self.stop()
            for button in self.result_buttons:
                button.destroy()
        for counter in (self.result_counter, self.cache_counter, self.request_counter):
            counter.set(0)
        self.outer_thread = OuterThread(self, name=f'ColumnThread{self.id}')
        self.outer_thread.start()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.columns = []
        self.input_area = InputArea(self)
        self.input_area.pack(side='top', expand=False, fill='x')
        ttk.Separator(self).pack(side='top', expand=False, fill='x')
        self.result_area = ResultArea(self)
        self.result_area.pack(side='top', expand=True, fill='both')
        self.button_creation_lock = th.Lock()
        self.input_area.check_health()


def quit_():
    if any(column.outer_thread for column in app.columns):
        for column in app.columns:
            column.stop()
    app.after(1500, lambda: app.quit())


if __name__ == '__main__':
    app = App()
    app.title('Log searcher')
    app.geometry('1400x500')
    app.protocol("WM_DELETE_WINDOW", quit_)
    app.mainloop()
