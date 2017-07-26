import requests
import json
import os
from abc import ABC, abstractmethod
from contextlib import contextmanager

from prompt_toolkit.shortcuts import prompt
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.contrib.completers import WordCompleter

import colorama


commands = {}


def command(c):
    """Decorator that creates an instance of the Command and registers it under
    its name and aliases."""
    instance = c()
    commands[instance.name] = instance
    for alias in instance.aliases:
        commands[alias] = instance


def find_command(tokens):
    """Looks up a command based on tokens and returns the command if it was
    found or None if it wasn't.."""
    if len(tokens) >= 3 and tokens[1] == '=':
        var_name = tokens[0]
        rvalue, args2 = find_command(tokens[2:])
        return AssignCommand(var_name, rvalue), args2
    elif tokens[0] in commands:
        return commands[tokens[0]], tokens[1:]
    else:
        return None, tokens


class HistoryFile(ABC):
    """Loads history from and saves history to a file."""

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
    """Loads and saves command history."""

    HISTORY_FILE = "~/.httpsh_history"

    def __init__(self):
        super().__init__(CommandHistory.HISTORY_FILE)

    def _from_file(self, file):
        return [line.strip() for line in file.readlines()]


class PayloadHistory(HistoryFile):
    """Loads and saves payload history."""

    HISTORY_FILE = "~/.httpsh_payload_history"

    def __init__(self):
        super().__init__(PayloadHistory.HISTORY_FILE)

    def _from_file(self, file):
        return [line.strip().replace('\n', '') for line in file.readlines()]


class Environment(object):
    """The shell's environment."""

    def __init__(self, history):
        self.host = None
        self.history = history
        self.headers = {}
        self.variables = {}


class History(object):
    """Group all history objects together."""

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
    """Context manager that loads history and runs some code inside the context
    of that history.  When the code completes, the history is saved."""
    history = History()
    try:
        history.load()
        yield history
    except Exception:
        pass
    history.save()


class Result(ABC):

    @abstractmethod
    def display(self):
        pass

    @abstractmethod
    def type(self):
        pass


class HostResult(Result):

    TYPE = 'host'

    def __init__(self, host):
        self.host = host

    def display(self):
        print(self.host)

    def type(self):
        return HostResult.TYPE


class NoResult(Result):

    def __init__(self):
        pass

    def display(self):
        pass

    def type(self):
        return None


class TextResult(Result):

    def __init__(self, text):
        self.text = text

    def display(self):
        print(self.text)

    def type(self):
        return 'text'


class Response(Result):

    TYPE = 'response'

    def __init__(self, resp):
        self.resp = resp

    def display(self):
        self._print_status_code()
        self._print_resp_headers()
        if self.is_json():
            print(json.dumps(self.resp.json(), sort_keys=True, indent=4))
        else:
            print(self.resp.text)

    def _print_resp_headers(self):
        for key in sorted(self.resp.headers.keys()):
            print("%s%s: %s%s" % (
                colorama.Style.BRIGHT, key, self.resp.headers[key],
                colorama.Style.NORMAL))

    def _print_status_code(self):
        print("%s%s%d%s%s" % (
            colorama.Style.BRIGHT,
            colorama.Fore.RED if self.resp.status_code > 300
            else colorama.Fore.GREEN,
            self.resp.status_code, colorama.Fore.RESET, colorama.Style.NORMAL))

    def type(self):
        return Response.TYPE

    def is_json(self):
        if 'content-type' in self.resp.headers:
            content_type = self.resp.headers['content-type'].lower()
            return (content_type.startswith('application/json') or
                    content_type.startswith('application/hal+json'))
        else:
            return False

    def json(self):
        return self.resp.json() if self.is_json() else None


class Command(ABC):

    def __init__(self, name, aliases):
        self.name = name
        self.aliases = aliases

    @abstractmethod
    def execute(self, arguments, environment):
        pass


class AssignCommand(Command):

    def __init__(self, var_name, command):
        super().__init__('=', [])
        self.var_name = var_name
        self.command = command

    def execute(self, arguments, env):
        result = self.command.execute(arguments, env)
        env.variables[self.var_name] = result
        return result


class HttpCommand(Command):

    def __init__(self, name, aliases, method):
        super().__init__(name, aliases)
        self.method = method

    def execute(self, arguments, env):
        if env.host:
            return Response(requests.request(
                    self.method,
                    env.host + arguments[0],
                    headers=env.headers,
                    json=self.get_payload()))
        else:
            return TextResult('please specify a host.')

    def get_payload(self):
        return None


@command
class GetCommand(HttpCommand):

    def __init__(self):
        super().__init__('get', ['g'], 'get')


class PayloadCommand(HttpCommand):

    def __init__(self, name, aliases, method):
        super().__init__(name, aliases, 'put')

    def get_payload(self):
        payload = prompt(
                'Enter Payload: ',
                history=self.env.hist.payload_history.get())
        json_payload = json.loads(payload)
        return json_payload


@command
class PutCommand(PayloadCommand):

    def __init__(self):
        super().__init__('put', ['pu'], 'get')


@command
class PostCommand(PayloadCommand):

    def __init__(self):
        super().__init__('post', ['po'], 'post')


@command
class DeleteCommand(HttpCommand):

    def __init__(self):
        super().__init__('delete', ['del'], 'delete')


@command
class HeadersCommand(Command):

    def __init__(self):
        super().__init__('headers', ['hs'])

    def execute(self, args, env):
        if len(args) == 0:
            return TextResult(self._get_headers(env))
        else:
            return TextResult('headers has no arguments')

    def _format_header(self, key, value):
        return ("%s%s: %s%s" % (
            colorama.Style.BRIGHT, key, value, colorama.Style.NORMAL))

    def _get_headers(self, env):
        return '\n'.join(
                self._format_header(key, env.headers[key])
                for key in sorted(env.headers.keys()))


@command
class HeaderCommand(Command):
    """Sets or displays the value of a header."""

    def __init__(self):
        super().__init__('header', ['hd'])

    def execute(self, args, env):
        if len(args) == 1:
            try:
                return TextResult(env.headers[args[0]])
            except Exception as ex:
                return TextResult("unknown header: %s" % args[0])
        elif len(args) == 2:
            env.headers[args[0]] = args[1]
            return NoResult()
        else:
            return TextResult('usage: header NAME [VALUE]')


@command
class TypeCommand(Command):
    """displays the type of a variable"""

    def __init__(self):
        super().__init__('type', ['t'])

    def execute(self, args, env):
        if args[0] in env.variables:
            return TextResult(env.variables[args[0]].type())
        else:
            return TextResult("unknown variable: %s" % args[0])


@command
class LinksCommand(Command):
    """Prints links found in a JSON response."""

    def __init__(self):
        super().__init__('links', [])

    def execute(self, args, env):
        if args[0] in env.variables:
            value = env.variables[args[0]]
            if value.type() == Response.TYPE:
                return TextResult('\n'.join(self._get_links(value)))
        else:
            return TextResult("unknown variable: %s" % args[0])

    def _get_links(self, resp):
        if resp.is_json():
            if '_links' in resp.json():
                for key in resp.json()['_links'].keys():
                    yield '%s: %s' % (key, resp.json()['_links'][key])


@command
class EnvCommand(Command):
    """Displays the environment."""

    def __init__(self):
        super().__init__('env', [])

    def execute(self, args, env):
        return TextResult(
                ('host: %s\n' % (env.host or '')) +
                ('headers:\n' +
                 ''.join('  %s: %s\n' % (k, v) for k, v
                         in env.headers.items())) +
                ('variables: %s' % (', '.join(env.variables.keys()))))


@command
class HostCommand(Command):
    "Displays or sets the current host."""

    def __init__(self):
        super().__init__('host', ['h'])

    def execute(self, args, env):
        if len(args) == 1:
            env.host = self._get_host(args[0])
            return HostResult(env.host)
        elif env.host:
            return HostResult(env.host)
        else:
            return TextResult("no host.  Try: host HOSTNAME")

    def _get_host(self, host):
        if not host.startswith('http'):
            completer = WordCompleter(['http', 'https'])
            text = prompt('Enter Schema [http/HTTPS]: ',
                          completer=completer) or 'https'
            return text + '://' + host
        return host


def main():
    colorama.init()
    with history() as hist:
        env = Environment(hist)
        while True:
            try:
                input = prompt('-> ',
                               history=env.history.command_history.get(),
                               auto_suggest=AutoSuggestFromHistory()).strip()
                if input:
                    tokens = input.split(' ')
                    command, args = find_command(tokens)
                    if command:
                        result = command.execute(args, env)
                        if result:
                            result.display()
                    elif len(tokens) == 1 and tokens[0] in env.variables:
                        env.variables[tokens[0]].display()
                    elif input:
                        print('unknown command or variable: %s' % input)
            except KeyboardInterrupt:
                break
            except EOFError:
                break
            except:
                import traceback
                traceback.print_exc()


if __name__ == '__main__':
    main()
