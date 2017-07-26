import requests
import json
import os
from abc import ABC, abstractmethod
from contextlib import contextmanager

from prompt_toolkit.shortcuts import prompt
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
import colorama

colorama.init()


class HistoryFile(ABC):

    def __init__(self, file_name):
        self.file_name = os.path.expanduser(file_name)
        self.history = InMemoryHistory()

    def get(self):
        return self.history

    def load(self):
        if os.path.exists(self.file_name):
            with open(self.file_name, 'r') as file:
                self.history.strings.extend(self._from_file(file))

    def save(self):
        with open(self.file_name, 'w') as file:
            for line in self._to_lines():
                file.write(line + '\n')

    def _to_lines(self):
        return self.history.strings

    @abstractmethod
    def _from_file(self, file):
        pass


class CommandHistory(HistoryFile):

    HISTORY_FILE = "~/.httpsh_history"

    def __init__(self):
        super().__init__(CommandHistory.HISTORY_FILE)

    def _from_file(self, file):
        return [line.strip() for line in file.readlines()]


class PayloadHistory(HistoryFile):

    HISTORY_FILE = "~/.httpsh_payload_history"

    def __init__(self):
        super().__init__(PayloadHistory.HISTORY_FILE)

    def _from_file(self, file):
        return [line.strip().replace('\n', '') for line in file.readlines()]


class History(object):

    def __init__(self):
        self.command_history = CommandHistory()
        self.payload_history = PayloadHistory()

    def load(self):
        self.command_history.load()
        self.payload_history.load()

    def save(self):
        self.command_history.save()
        self.payload_history.save()


@contextmanager
def history():
    history = History()
    try:
        history.load()
        yield history
    except Exception:
        pass
    history.save()


def is_json(resp):
    if 'content-type' in resp.headers:
        content_type = resp.headers['content-type'].lower()
        return (content_type.startswith('application/json') or
                content_type.startswith('application/hal+json'))
    else:
        return False


def print_headers(headers):
    for key in sorted(headers.keys()):
        print("%s%s: %s%s" % (
            colorama.Style.BRIGHT, key, headers[key],
            colorama.Style.NORMAL))


def print_resp_headers(resp):
    for key in sorted(resp.headers.keys()):
        print("%s%s: %s%s" % (
            colorama.Style.BRIGHT, key, resp.headers[key],
            colorama.Style.NORMAL))


def print_status_code(resp):
    print("%s%s%d%s%s" % (
        colorama.Style.BRIGHT,
        colorama.Fore.RED if resp.status_code > 300 else colorama.Fore.GREEN,
        resp.status_code, colorama.Fore.RESET, colorama.Style.NORMAL))


def print_response(resp):
    print_status_code(resp)
    print_resp_headers(resp)
    if is_json(resp):
        print(json.dumps(resp.json(), sort_keys=True, indent=4))
    else:
        print(resp.text)


def get_host(host):
    if not host.startswith('http'):
        print("no schema, assuming https://")
        return 'https://' + host
    else:
        return host


def prompt_for_payload(payload_history):
    payload = prompt('Enter Payload: ', history=payload_history)
    json_payload = json.loads(payload)
    return json_payload


def send_request(method, url, headers, payload=None):
    try:
        resp = requests.request(method, url, headers=headers,
                                json=payload)
        print_response(resp)
    except Exception as ex:
        print(ex)


def main():
    host = ''
    headers = {}

    with history() as hist:
        while True:
            try:
                input = prompt('-> ',
                               history=hist.command_history.get(),
                               auto_suggest=AutoSuggestFromHistory())

                tokens = input.split(' ')

                if len(tokens) == 0:
                    continue

                command = tokens[0]

                if command == 'host':
                    host = get_host(tokens[1])

                elif command == 'headers':
                    print_headers(headers)

                elif command == 'header':
                    if len(tokens) == 3:
                        headers[tokens[1]] = tokens[2]
                    else:
                        print("header NAME VALUE [NAME VALUE]...")

                elif command == 'rm' and tokens[1] == 'headers':
                    headers.clear()

                elif command == 'get':
                    send_request('get', host + tokens[1], headers)

                elif command == 'post':
                    payload = prompt_for_payload(hist.payload_history.get())
                    if payload:
                        send_request('post', host + tokens[1], headers,
                                     payload=payload)

                elif command == 'put':
                    payload = prompt_for_payload(hist.payload_history.get())
                    if payload:
                        send_request('put', host + tokens[1], headers,
                                     payload=payload)

                elif command == 'delete':
                    send_request('delete', host + tokens[1], headers)

                elif command == 'quit' or command == 'exit':
                    break
            except KeyboardInterrupt:
                break
            except Exception as ex:
                print(ex)


if __name__ == '__main__':
    main()
