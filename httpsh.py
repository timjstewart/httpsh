import datetime
import json
import os
import requests
import sys

from abc import ABC, abstractmethod
from contextlib import contextmanager

import colorama

from prompt_toolkit.shortcuts import prompt
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.contrib.completers import WordCompleter


commands = {}


def command(c):
    """Decorator that creates an instance of the decorated Command class and
    registers it under its name and aliases."""
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
        self.elapsed = None

    def display(self):
        self._print_resp_headers()
        if self.is_json():
            try:
                print(json.dumps(self.resp.json(), sort_keys=True, indent=4))
            except json.decoder.JSONDecodeError:
                if self.resp.text.strip():
                    print("could not decode response as JSON: %s" %
                          self.resp.text)
        elif self.resp.text.strip():
            print(self.resp.text)
        self._print_status_code()
        sys.stdout.write(': ')
        self._print_content_length(self.resp)
        sys.stdout.write(' in ')
        self._print_elapsed_time()
        sys.stdout.write('\n')

    def _print_content_length(self, resp):
        sys.stdout.write("%s%s%s bytes" % (
            colorama.Style.BRIGHT, len(resp.content),
            colorama.Style.NORMAL))

    def _print_elapsed_time(self):
        sys.stdout.write("%s%.3f%s seconds" % (
            colorama.Style.BRIGHT, self.elapsed.total_seconds(),
            colorama.Style.NORMAL))

    def _print_resp_headers(self):
        for key in sorted(self.resp.headers.keys()):
            print("%s%s%s: %s" % (
                colorama.Style.BRIGHT, key, colorama.Style.NORMAL,
                self.resp.headers[key]))

    def _print_status_code(self):
        sys.stdout.write("%s%s%d%s%s" % (
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


class Input(ABC):

    @abstractmethod
    def get(self, prompt_text):
        pass

    @abstractmethod
    def display_command(self, command, args):
        pass


class ConsoleInput(Input):

    def __init__(self, history):
        self.history = history

    def get(self, prompt_text):
        return prompt(prompt_text, history=self.history)

    def display_command(self, command, args):
        pass


class FileInput(Input):

    def __init__(self, file):
        self.file = file

    def get(self, prompt_text):
        return self.file.readline()

    def display_command(self, command, args):
        print("%s>> %s %s%s" % (
            colorama.Fore.BLUE, command.name,
            ' '.join(args), colorama.Fore.RESET))


class Command(ABC):

    def __init__(self, name, aliases):
        self.name = name
        self.aliases = aliases

    @abstractmethod
    def execute(self, input, arguments, environment):
        pass


@command
class HelpCommand(Command):
    """displays help screen."""

    def __init__(self):
        super().__init__('help', ['?'])

    def execute(self, input, arguments, environment):
        if arguments:
            # Lookup help for the specified command
            command, args = find_command(arguments)
            if command:
                print(" %s" % self._format_doc_string(command))
        else:
            # Lookup help for all commands
            for command in sorted(set(commands.values()),
                    key=lambda x: x.name):
                print(" %s" % self._format_doc_string(command))

    def _format_doc_string(self, command):
            return "%-7s - %s" % (command.name, command.__doc__)


@command
class LoadCommand(Command):
    """loads a file and executes commands from it."""

    def __init__(self):
        super().__init__('load', [])

    def execute(self, _, arguments, environment):
        if arguments:
            file_name = arguments[0]
            with open(os.path.expanduser(file_name), 'r') as script:
                input = FileInput(script)
                start = datetime.datetime.now()
                while True:
                    line = script.readline()
                    if not line:
                        break
                    read_execute_display(line.strip(), input, environment)
                elapsed = datetime.datetime.now() - start
                print("Script ran in: %s%.3f%s seconds" % (
                    colorama.Style.BRIGHT, elapsed.total_seconds(),
                    colorama.Style.NORMAL))


class AssignCommand(Command):

    def __init__(self, var_name, command):
        super().__init__('=', [])
        self.var_name = var_name
        self.command = command

    def execute(self, input, arguments, env):
        result = self.command.execute(input, arguments, env)
        env.variables[self.var_name] = result
        return result


class HttpCommand(Command):

    def __init__(self, name, aliases, method):
        super().__init__(name, aliases)
        self.method = method

    def execute(self, input, arguments, env):
        if env.host:
            start = datetime.datetime.now()
            resp = Response(requests.request(
                    self.method,
                    env.host + arguments[0],
                    headers=env.headers,
                    json=self.get_payload(input, env)))
            resp.elapsed = datetime.datetime.now() - start
            return resp
        else:
            return TextResult('please specify a host.')

    def get_payload(self, input, env):
        return None


@command
class HeadCommand(HttpCommand):
    """sends a HEAD request using the current value of host and headers."""

    def __init__(self):
        super().__init__('head', [], 'head')


@command
class OptionsCommand(HttpCommand):
    """sends a OPTIONS request using the current value of host and headers."""

    def __init__(self):
        super().__init__('options', ['opt'], 'options')


@command
class GetCommand(HttpCommand):
    """sends a GET request using the current value of host and headers."""

    def __init__(self):
        super().__init__('get', ['g'], 'get')


class PayloadCommand(HttpCommand):

    def __init__(self, name, aliases, method):
        super().__init__(name, aliases, 'put')

    def get_payload(self, input, env):
        payload = input.get('Enter Payload: ')
        json_payload = json.loads(payload)
        return json_payload


@command
class PutCommand(PayloadCommand):
    """sends a PUT request using the current value of host and headers."""

    def __init__(self):
        super().__init__('put', ['pu'], 'get')


@command
class PostCommand(PayloadCommand):
    """sends a POST request using the current value of host and headers."""

    def __init__(self):
        super().__init__('post', ['po'], 'post')


@command
class PatchCommand(PayloadCommand):
    """sends a PATCH request using the current value of host and headers."""

    def __init__(self):
        super().__init__('patch', ['pat'], 'patch')


@command
class DeleteCommand(HttpCommand):
    """sends a DELETE request using the current value of host and headers."""

    def __init__(self):
        super().__init__('delete', ['del'], 'delete')


@command
class HeadersCommand(Command):
    """shows the current list of headers."""

    def __init__(self):
        super().__init__('headers', ['hs'])

    def execute(self, input, args, env):
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
    """sets or displays the value of a header."""

    def __init__(self):
        super().__init__('header', ['hd'])

    def execute(self, input, args, env):
        if len(args) == 1:
            try:
                return TextResult(env.headers[args[0]])
            except Exception:
                return TextResult("unknown header: %s" % args[0])
        elif len(args) == 2:
            env.headers[args[0]] = args[1]
            return NoResult()
        else:
            return TextResult('usage: header NAME [VALUE]')


@command
class TypeCommand(Command):
    """displays the type of a variable."""

    def __init__(self):
        super().__init__('type', ['t'])

    def execute(self, input, args, env):
        if args[0] in env.variables:
            return TextResult(env.variables[args[0]].type())
        else:
            return TextResult("unknown variable: %s" % args[0])


@command
class LinksCommand(Command):
    """prints links found in a JSON response."""

    def __init__(self):
        super().__init__('links', [])

    def execute(self, input, args, env):
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
    """displays the environment."""

    def __init__(self):
        super().__init__('env', [])

    def execute(self, input, args, env):
        return TextResult(
                ('host: %s\n' % (env.host or '')) +
                ('headers:\n' +
                 ''.join('  %s: %s\n' % (k, v) for k, v
                         in env.headers.items())) +
                ('variables: %s' % (', '.join(env.variables.keys()))))


@command
class HostCommand(Command):
    "displays or sets the current host."""

    def __init__(self):
        super().__init__('host', ['h'])

    def execute(self, input, args, env):
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


def read_command(line):
    """Reads a command from a string and returns it."""
    if line.strip():
        return find_command(line.split(' '))
    else:
        return None, line


def execute_command(command, input, args, env):
    """Executes a command in an environment."""
    return command.execute(input, args, env)


def display_command_result(result):
    result.display()


def read_execute_display(line, input, env):
    command, args = read_command(line)
    if command:
        input.display_command(command, args)
        result = execute_command(command, input, args, env)
        if result:
            display_command_result(result)
        return True
    else:
        return False


def main():
    colorama.init()
    with history() as hist:
        env = Environment(hist)
        console = ConsoleInput(env.history.command_history.get())
        while True:
            try:
                input = prompt('-> ',
                               history=env.history.command_history.get(),
                               auto_suggest=AutoSuggestFromHistory()).strip()
                if not read_execute_display(input, console, env):
                    tokens = input.split(' ')
                    if len(tokens) == 1 and tokens[0] in env.variables:
                        env.variables[tokens[0]].display()
                    elif input:
                        print('unknown command or variable: %s' % input)
            except KeyboardInterrupt:
                print("Ctrl-D to quit.")
            except EOFError:
                print("bye.")
                break
            except:
                import traceback
                traceback.print_exc()


if __name__ == '__main__':
    main()
