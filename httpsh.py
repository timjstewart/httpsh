import copy
import datetime
import itertools
import json
import os
import re
import statistics
import sys

from abc import ABC, abstractmethod
from typing import (Optional, Dict, Tuple, Sequence, Any, Mapping, List, Union,
                    cast)
from typing import TextIO

import colorama
import requests

from prompt_toolkit.shortcuts import prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.contrib.completers import WordCompleter
from pygments import highlight, lexers, formatters


VERSION = (0, 0, 2)

JsonValue = Union[int, bool, float, List[Any], Dict[str, Any]]


class Category(object):
    JSON = 'JSON'
    ENVIRONMENT = 'environment'
    HOSTS = 'hosts'
    MISC = 'misc'
    HTTP = 'HTTP'
    REQUESTS = 'requests'


class Value(ABC):

    @abstractmethod
    def display(self) -> None:
        pass

    @abstractmethod
    def summary(self) -> str:
        pass

    @abstractmethod
    def type(self) -> str:
        pass


class History(object):
    """Group all history objects together."""

    COMMAND_HISTORY_FILE = "~/.httpsh_history"
    PAYLOAD_HISTORY_FILE = "~/.httpsh_payload_history"

    def __init__(self) -> None:
        self.command_history = FileHistory(
                os.path.expanduser(History.COMMAND_HISTORY_FILE))
        self.payload_history = FileHistory(
                os.path.expanduser(History.PAYLOAD_HISTORY_FILE))


class Host(Value):

    TYPE = 'host'

    def __init__(self, alias: str, hostname: str) -> None:
        self.alias = alias
        self.hostname = hostname
        self.headers = {}  # type: Dict[str, str]

    def display(self) -> None:
        print(style(bold(self.hostname)))
        for header in sorted(self.headers.keys()):
            print('  %s: %s' % (style(bold(header)), self.headers[header]))

    def summary(self) -> str:
        return "hostname = %s" % self.hostname

    def type(self) -> str:
        return Host.TYPE

    def remove_header(self, name: str) -> Optional[str]:
        return self.headers.pop(name, None)

    def add_header(self, name: str, value: str) -> None:
        self.headers[name] = value


class Environment(object):
    """The shell's environment."""

    def __init__(self, history: History) -> None:
        self.host = None  # type: Host
        self.history = history
        self.variables = {}  # type: Dict[str, Value]

    def bind(self, name: str, value: Value) -> Value:
        if value:
            self.variables[name] = value
        else:
            self.variables.pop(name, None)
        return value

    def unbind(self, name: str) -> Optional[Value]:
        return self.variables.pop(name, None)

    def lookup(self, var_name: str, var_type: str=None) -> Value:
        if var_name not in self.variables:
            raise KeyError('no variable named: %s' % var_name)
        var = self.variables[var_name]
        if var_type and var.type() != var_type:
            raise ValueError('variable: %s has type: %s not %s' % (
                var_name, var.type(), var_type))
        return var


class IO(ABC):

    @abstractmethod
    def get_command(self, prompt_text: str) -> str:
        pass

    @abstractmethod
    def get_payload(self, prompt_text: str) -> JsonValue:
        pass

    @abstractmethod
    def display_command(self, command: str, args: Sequence[str]) -> None:
        pass


class Command(ABC):

    def __init__(self, name: str, aliases: Sequence[str],
                 category: str = 'misc') -> None:
        self.name = name
        self.aliases = aliases
        self.category = category

    @abstractmethod
    def evaluate(self, input: IO, arguments: Sequence[str],
                 environment: Environment,
                 value: Optional[Value] = None) -> Value:
        pass

    def is_assignable(self) -> bool:
        return False


class ConsoleIO(IO):

    def __init__(self, env: Environment) -> None:
        self.env = env

    def get_payload(self, prompt_text: str) -> JsonValue:
        return json.loads(prompt(
                prompt_text,
                auto_suggest=AutoSuggestFromHistory(),
                history=self.env.history.payload_history).strip())

    def get_command(self, prompt_text: str) -> str:
        return prompt(
                prompt_text,
                auto_suggest=AutoSuggestFromHistory(),
                history=self.env.history.command_history).strip()

    def display_command(self, command: str, args: Sequence[str]) -> None:
        pass


class FileIO(IO):
    """Reads commands from a file."""

    def __init__(self, file: TextIO) -> None:
        self.file = file

    def get_command(self, prompt_text: str) -> str:
        line = self.file.readline()
        return line.strip() if line else None

    def get_payload(self, prompt_text: str) -> JsonValue:
        line = self.file.readline()
        return json.loads(line.strip()) if line else None

    def display_command(self, command: str, args: Sequence[str]) -> None:
        print(style(blue(">> %s %s" % (command, ' '.join(args)))))


commands = {}  # type: Dict[str, Command]
aliased_commands = {}  # type: Dict[str, Command]


def format_json(d: JsonValue) -> str:
    """Formats a dictionary as a JSON string."""
    return json.dumps(d, sort_keys=True, indent=3)


def colorize_json(j: str) -> str:
    """Adds color to a JSON string."""
    return highlight(j, lexers.JsonLexer(), formatters.TerminalFormatter())


def pretty_json(d: JsonValue) -> str:
    """returns a pretty rendition of a dictionary."""
    return colorize_json(format_json(d))


def style(s: str) -> str:
    return str(s) + colorama.Style.RESET_ALL


def bold(s: str) -> str:
    return colorama.Style.BRIGHT + str(s)


def blue(s: str) -> str:
    return colorama.Fore.BLUE + str(s)


def red(s: str) -> str:
    return colorama.Fore.RED + str(s)


def green(s: str) -> str:
    return colorama.Fore.GREEN + str(s)


def command(command_class):
    """Decorator that creates an instance of the decorated Command class and
    registers it under its name and aliases."""
    cmd = command_class()
    commands[cmd.name] = aliased_commands[cmd.name] = cmd
    for alias in cmd.aliases:
        aliased_commands[alias] = cmd
    return command_class


class NullValue(Value):

    def __init__(self) -> None:
        pass

    def display(self) -> None:
        pass

    def summary(self) -> str:
        return "null"

    def type(self) -> str:
        return None


class StringValue(Value):

    COLORS = {
            'red': colorama.Fore.RED,
            'blue': colorama.Fore.BLUE,
            'green': colorama.Fore.GREEN,
            'yellow': colorama.Fore.YELLOW,
            }

    BGCOLORS = {
            'red': colorama.Back.RED,
            'blue': colorama.Back.BLUE,
            'green': colorama.Back.GREEN,
            'yellow': colorama.Back.YELLOW,
            }

    def __init__(self, text: str, bold: bool = False,
                 color: str = None, bgcolor: str = None) -> None:
        self.text = text.strip()
        self.bold = bold
        self.color = color
        self.bgcolor = bgcolor

    def summary(self) -> str:
        return self._style(self.text[:20] +
                           ('...' if len(self.text) > 20 else ''))

    def display(self) -> None:
        if self.text:
            print(self._style(self.text))

    def type(self) -> str:
        return 'text'

    def _style(self, text: str) -> str:
        result = ''
        if self.bold:
            result += colorama.Style.BRIGHT
        if self.color:
            result += StringValue.COLORS[self.color]
        if self.bgcolor:
            result += StringValue.BGCOLORS[self.bgcolor]
        result += text
        result += colorama.Style.RESET_ALL
        return result

    def __str__(self):
        return self._style(self.text)


class ErrorString(StringValue):

    def __init__(self, text: str, severe: bool = False) -> None:
        super().__init__(text, bold=True,
                         color=('red' if severe else 'yellow'))


class Response(Value):

    TYPE = 'response'

    def __init__(self, resp) -> None:
        self.resp = resp
        self.elapsed = None  # type: datetime.timedelta

    def summary(self) -> str:
        return "status: %d, length: %d" % (
                self.resp.status_code,
                len(self.resp.content))

    def display(self) -> None:
        self._print_resp_headers()
        if self.is_json():
            try:
                print(pretty_json(self.resp.json()))
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

    def _print_content_length(self, resp) -> None:
        sys.stdout.write("%s bytes" % style(bold(str(len(resp.content)))))

    def _print_elapsed_time(self) -> None:
        sys.stdout.write("%s seconds" %
                         style(bold("%.3f" % self.elapsed.total_seconds())))

    def _print_resp_headers(self) -> None:
        for key in sorted(self.resp.headers.keys()):
            print("%s: %s" %
                  (style(bold(key)), self.resp.headers[key]))

    def _print_status_code(self) -> None:
        if self.resp.status_code >= 400:
            status_code = style(red(bold(self.resp.status_code)))
        else:
            status_code = style(green(bold(self.resp.status_code)))
        sys.stdout.write("%s" % status_code)

    def type(self) -> str:
        return Response.TYPE

    def is_json(self) -> bool:
        if 'content-type' in self.resp.headers:
            content_type = self.resp.headers['content-type'].lower()
            return (content_type.startswith('application/json') or
                    content_type.startswith('application/hal+json'))
        else:
            return False

    def json(self) -> Dict:
        return self.resp.json() if self.is_json() else None


def find_command(tokens: Sequence[str]) -> Tuple[Command, Sequence[str]]:
    """Looks up a command based on tokens and returns the command if it was
    found or None if it wasn't.."""
    if len(tokens) >= 3 and tokens[1] == '=':
        var_name = tokens[0]
        command_string = tokens[2:]
        rvalue, args2 = find_command(command_string)
        if not rvalue:
            raise KeyError('could not find command: %s' %
                           ' '.join(command_string))
        return AssignCommand(var_name, rvalue), args2
    elif tokens[0] in aliased_commands:
        return aliased_commands[tokens[0]], tokens[1:]
    else:
        return None, tokens


@command
class HelpCommand(Command):
    """displays help screen."""

    def __init__(self) -> None:
        super().__init__('help', ['?'])

    def evaluate(self, input: IO, arguments: Sequence[str],
                 environment: Environment,
                 value: Optional[Value] = None):
        if arguments:
            # Lookup help for the specified command
            command, args = find_command(arguments)
            if command:
                print("%s" % self._format_doc_string_long(command))
        else:
            # Lookup help for all commands
            grouped = itertools.groupby(
                    sorted(commands.values(), key=lambda x: x.category),
                    key=lambda x: x.category)

            print("httpsh v%d.%d.%d" % VERSION)
            print("The following are commands that you can enter in " +
                  "the shell, grouped by category:\n")
            for group in grouped:
                print(style(bold(group[0] + ':')))
                for command in sorted(group[1], key=lambda x: x.name):
                    print("  %s" % self._format_doc_string_short(command))
                print()
        return NullValue()

    def _format_doc_string_short(self, command: Command) -> str:
        return "%-8s - %s" % (
                command.name, command.__doc__.split('\n')[0])

    def _format_doc_string_long(self, command: Command) -> str:
        doc = "%s - %s" % (command.name, command.__doc__)
        if command.aliases:
            doc += '\nAliases: %s' % ', '.join(command.aliases)
        return doc


@command
class RunCommand(Command):
    """evaluates commands stored in a file.

For example:

    -> run script
    -> run ~/script
    """

    def __init__(self) -> None:
        super().__init__('run', ['.', 'source'])

    def evaluate(self, _, arguments: Sequence[str],
                 environment: Environment,
                 value: Optional[Value] = None) -> Value:
        if arguments:
            file_name = arguments[0]
            with open(os.path.expanduser(file_name), 'r') as script:
                input = FileIO(script)
                start = datetime.datetime.now()
                while True:
                    success, result = read_eval_print(input, environment)
                    if not success and not result:
                        break
                elapsed = datetime.datetime.now() - start
                return StringValue("Script ran in: %.3f seconds" % (
                        elapsed.total_seconds()), bold=True)
        else:
            raise ValueError('usage: run SCRIPT_NAME')


class AssignCommand(Command):

    def __init__(self, var_name: str, command: Command) -> None:
        super().__init__(command.name, [])
        self.var_name = var_name
        self.command = command

    def evaluate(self, input, arguments: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if self.command.is_assignable():
            result = self.command.evaluate(input, arguments, env)
            env.bind(self.var_name, result)
            return result
        else:
            raise ValueError(
                    "the '%s' command is not assignable. (aliases: %s)" % (
                        self.command.name,
                        ', '.join(self.command.aliases)))


@command
class RepeatCommand(Command):
    """repeats a command n times and prints average time.

There is no delay between repetitions.

For example:

    -> repeat 100 get /customers
    -> repeat 10 run script
    """

    def __init__(self) -> None:
        super().__init__('repeat', [])

    def evaluate(self, input: IO, arguments: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        repetitions = int(arguments[0])
        command, cmd_args = find_command(arguments[1:])
        if command:
            times = []
            for _ in range(0, repetitions):
                start = datetime.datetime.now()
                result = command.evaluate(input, cmd_args, env)
                if result:
                    print_command_result(result)
                times.append((datetime.datetime.now() - start)
                             .total_seconds())
            return StringValue(
                    'Ran command: %d times.  Average time: %f seconds' % (
                        repetitions, statistics.mean(times)), bold=True)
        else:
            raise KeyError('unknown command: %s' % arguments)


@command
class SelectCommand(Command):
    """selects a sub-tree of a JSON response.

Given a Response named resp containing the following JSON:

    {
       "dog": {
           "name": "Fluffy",
           "nicknames": [ "Mr Fluffy", "Fluffster", "Fluffles" ],
           "breed": "Chihuahua"
        },
        "_links": []
    }

For example:

    display all of the JSON in the response.
    -> select resp

    display only the dog node of the JSON response.
    -> select resp dog

    display only the dog's nicknames.
    -> select resp dog.nicknames

    display all properties ending in name.
    -> select resp dog.*name

    display only the dog's name and breed.
    -> select resp dog.name,breed

    display dog's nicknames and its name.
    -> select resp dog(name).nicknames
    """

    def __init__(self) -> None:
        super().__init__('select', [], category=Category.JSON)

    def _get_resp(self, arguments: Sequence[Any],
                  value: Optional[Value],
                  env: Environment) -> Tuple[Response, Sequence[str]]:
        """Returns the Response object to run the select command on.
        That object can either be directly passed as the value parameter
        or it can be passed as a name of a variable presumably containing
        a Response value."""
        if isinstance(value, Response):
            return value, arguments
        if arguments:
            return (cast(Response, env.lookup(arguments[0], Response.TYPE)),
                    arguments[1:])
        raise ValueError('could not get Response from arguments')

    def evaluate(self, input, arguments: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        resp, arguments = self._get_resp(arguments, value, env)
        if resp:
            # make sure that the Response contains JSON.  You can't run
            # select on an HTML or XML document.
            if resp.is_json():
                if len(arguments) == 1:
                    return self._select(resp, arguments[0])
                else:
                    return resp
            else:
                return ErrorString("response is not JSON.")
        else:
            return ErrorString("usage select RESPONSE [SELECT_STATEMENT]")

    def _select(self, resp: Response, select_stmt: str) -> Value:
        """runs the select statement on a Response with a JSON payload.

        A select statement is a chain of expressions separated by period
        characters.  Each expression attempts to traverse deeper into the
        supplied JSON."""
        parts = select_stmt.split('.')
        patterns, collect = self._parse_expression(parts[0])
        if not patterns and collect and len(parts) >= 2:
            result = self._select_part(
                    resp.json(), parts[1], parts[2:], collect, {})
        else:
            result = self._select_part(
                    resp.json(), parts[0], parts[1:], [], {})
        return StringValue(pretty_json(result))

    def _matches(self, pattern: str, string: str) -> bool:
        """determines if the pattern from the select statement matches a
        particular string.  The patterns currently only support one special
        character, the asterisk, which acts like a '.*' in the language of
        regular expressions."""
        regex_pattern = '^' + pattern.replace('*', '.*') + '$'
        return bool(re.match(regex_pattern, string))

    def _get_matching_keys(self, node: Mapping[str, Any],
                           pattern: str) -> List[str]:
        """returns all keys in a dictionary that match the given pattern."""
        if type(node) == dict:
            return [key for key in node.keys()
                    if self._matches(pattern, key)]
        return []

    def _parse_expression(self,
                          expression: str) -> Tuple[List[str], List[str]]:
        m = re.match('([^(]*)(\\(([^)]*)\\))?', expression)
        patterns = [pattern for pattern in m.group(1).split(',')
                    if pattern.strip()]
        collect = ([item for item in m.group(3).split(',') if item.strip()]
                   if m.group(3) else [])
        return patterns, collect

    def _merge_dicts(self, collected: Dict[str, Any],
                     selected: Mapping[str, Any]) -> Dict[str, Any]:
            collected.update(selected)
            return collected

    def _verify_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if not result:
            raise KeyError('could not traverse any deeper into JSON.')
        else:
            return result

    def _select_part(self, node: JsonValue, part: str,
                     parts: Sequence[str], collect_here: Sequence[str],
                     collected: Dict[str, Any]) -> JsonValue:
        patterns, collect = self._parse_expression(part)
        if isinstance(node, dict):
            collected.update(
                    {key: node[key]
                     for c in collect_here
                     for key in self._get_matching_keys(node, c)})
            if parts:
                return self._verify_result(
                        {key: self._select_part(
                           node[key], parts[0], parts[1:], collect, collected)
                         for pattern in patterns
                         for key in self._get_matching_keys(node, pattern)})
            else:
                return self._merge_dicts(
                            collected,
                            self._verify_result(
                                {key: node[key]
                                 for pattern in patterns
                                 for key in self._get_matching_keys(
                                     node, pattern)}))
        elif isinstance(node, list):
            return [self._select_part(item, ','.join(patterns),
                                      parts, collect_here, collected)
                    for item in node]
        elif not parts and patterns == ['*']:
            return node
        else:
            # There are still more expressions in parts but we have navigated
            # as deeply into the JSON as possible.  The selection has failed.
            raise KeyError(('could not traverse any deeper into JSON ' +
                           ('(part=%s, parts=%s)' % (part, parts))))


class HttpCommand(Command):

    def __init__(self, name: str, aliases: Sequence[str],
                 method: str) -> None:
        super().__init__(name, aliases, category=Category.HTTP)
        self.method = method

    def is_assignable(self) -> bool:
        return True

    def to_curl_command(self, input: IO, arguments: Sequence[str],
                        env: Environment) -> str:
        if env.host:
            command = 'curl -X'
            command += self.method.upper()
            command += ' '
            # host
            command += '"' + env.host.hostname
            if arguments:
                if not arguments[0].startswith('/'):
                    command += '/'
                command += arguments[0]
            command += '"'
            # Prompt for payload if method can handle one.
            payload = self.get_payload(input, env)
            # Headers
            for name, value in env.host.headers.items():
                command += " -H '%s: %s'" % (name, value)
            if payload and 'Content-Type' not in env.host.headers:
                command += " -H 'Content-Type: application/json'"
            # Payload if provided
            if payload:
                command += " -d '" + json.dumps(payload) + "'"
            return command
        else:
            raise ValueError("no host.  try 'help host'")

    def evaluate(self, input: IO, arguments: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None,
                 host: Optional[Host] = None,
                 payload: JsonValue = None) -> Value:
        host = host or env.host
        if host:
            path = (arguments[0] if arguments else '/')
            path = ('/' + path) if not path.startswith('/') else path
            start = datetime.datetime.now()
            resp = Response(requests.request(
                    self.method,
                    host.hostname + path,
                    headers=host.headers,
                    json=payload or self.get_payload(input, env)))
            resp.elapsed = datetime.datetime.now() - start

            if len(arguments) > 2 and arguments[1] == '|':
                # filter the HTTP response through a select statement
                select_stmt = arguments[2]
                return aliased_commands['select'].evaluate(
                        input, [select_stmt], env, value=resp)
            else:
                return resp
        else:
            return ErrorString('please specify a host.')

    def get_payload(self, input: IO, env: Environment) -> JsonValue:
        pass


class Request(Value):

    TYPE = 'request'

    def __init__(self, host: Host, command: HttpCommand,
                 path: str, payload: str = None) -> None:
        self.host = copy.deepcopy(host)
        self.command = command
        self.path = path
        self.payload = payload

    def display(self) -> None:
        print(style(bold('%s Request:' % self.command.method.upper())))
        self.host.display()
        print(style(bold('Path: ')) + self.path)
        if self.payload:
            print(style(bold('Payload: ')) + json.dumps(self.payload))

    def summary(self) -> str:
        return "method: %s, host: %s, path: %s" % (
                self.command.method.upper(), self.host.hostname, self.path)

    def type(self) -> str:
        return Request.TYPE


@command
class RequestCommand(Command):
    """creates an HTTP request that can be run multiple times.

Example:

    -> request get /dogs
    -> get_dogs = request get /dogs
    """

    def __init__(self) -> None:
        super().__init__('request', ['@'], category=Category.REQUESTS)

    def is_assignable(self) -> bool:
        return True

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if not env.host:
            raise ValueError("no host.  try 'help host'")
        command, cmd_args = find_command(args)
        if isinstance(command, PayloadCommand):
            return Request(env.host, command, cmd_args[0],
                           payload=input.get_payload('Enter Payload: '))
        if isinstance(command, HttpCommand):
            return Request(env.host, command, cmd_args[0])
        return ErrorString("'%s' is not an HTTP command (e.g. get, post)" %
                           ' '.join(args))


@command
class SendCommand(Command):
    """sends a request and prints its results.

Example:

    # A little setup first...
    -> req = get /dogs?breed=Pug

    # Send the request.
    -> send req

    # Send the request again.
    -> send req

    # Send the request and filter it through a select statement.
    -> send req | content.breed
    """

    def __init__(self) -> None:
        super().__init__('send', ['!'], category=Category.REQUESTS)

    def is_assignable(self) -> bool:
        return True

    def _get_request(self, args: Sequence[str], value: Optional[Value],
                     env: Environment) -> Optional[Request]:
        if isinstance(value, Request):
            return cast(Request, value)
        elif args:
            value = env.lookup(args[0])
            if not value:
                raise KeyError("no variable named: %s" % args[0])
            elif not isinstance(value, Request):
                raise ValueError("'%s' is not a Request" % args[0])
            else:
                return cast(Request, value)
        raise ValueError("usage: send VAR")

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        request = self._get_request(args, value, env)
        value = request.command.evaluate(input, [request.path],
                                         env, host=request.host,
                                         payload=request.payload)
        if value and len(args) > 2 and args[1] == '|':
            # filter the HTTP response through a select statement
            select_stmt = args[2]
            return aliased_commands['select'].evaluate(
                    input, [select_stmt], env, value=value)
        else:
            return value


@command
class CurlCommand(Command):
    """prints a curl command for the provided HTTP command (e.g. get, put).

You must have an active host.  Any headers added to the current host will be
included in the curl command.

For example:

    -> curl get /customers
    """

    def __init__(self) -> None:
        super().__init__('curl', ['HEAD'])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        command, cmd_args = find_command(args)
        if isinstance(command, HttpCommand):
            return StringValue(command.to_curl_command(input, cmd_args, env))
        return ErrorString("'%s' is not an HTTP command (e.g. get, post)" %
                           ' '.join(args))


@command
class HeadCommand(HttpCommand):
    """sends a HEAD request using the current value of host and headers.

For example:

    -> head /customers
    """

    def __init__(self) -> None:
        super().__init__('head', ['HEAD'], 'head')


@command
class OptionsCommand(HttpCommand):
    """sends a OPTIONS request using the current value of host and headers.

For example:

    -> options /customers
    """

    def __init__(self) -> None:
        super().__init__('options', ['OPTIONS', 'opt'], 'options')


@command
class GetCommand(HttpCommand):
    """sends a GET request using the current value of host and headers.

For example:

    -> get /customers
    """

    def __init__(self) -> None:
        super().__init__('get', ['GET', 'g'], 'get')


class PayloadCommand(HttpCommand):

    def __init__(self, name: str, aliases: Sequence[str],
                 method: str) -> None:
        super().__init__(name, aliases, method)

    def get_payload(self, input: IO, env: Environment) -> JsonValue:
        return input.get_payload('Enter Payload: ')


@command
class PutCommand(PayloadCommand):
    """sends a PUT request using the current value of host and headers.

For example:

    -> put /customers
    """

    def __init__(self) -> None:
        super().__init__('put', ['PUT', 'pu'], 'put')


@command
class PostCommand(PayloadCommand):
    """sends a POST request using the current value of host and headers.

For example:

    -> post /customers
    """

    def __init__(self) -> None:
        super().__init__('post', ['POST', 'po'], 'post')


@command
class GetWithPayloadCommand(PayloadCommand):
    """sends a GET request with a payload.

For example:

    -> getp /customers

Note: If you're wondering who would ever have a service where a GET request
sent a payload, check out ElasticSearch.
    """

    def __init__(self) -> None:
        super().__init__('getp', ['GETP', 'gp'], 'get')


@command
class PatchCommand(PayloadCommand):
    """sends a PATCH request using the current value of host and headers.

For example:

    -> patch /customers
    """

    def __init__(self) -> None:
        super().__init__('patch', ['pat'], 'patch')


@command
class DeleteCommand(HttpCommand):
    """sends a DELETE request using the current value of host and headers.

For example:

    -> delete /customers
    """

    def __init__(self) -> None:
        super().__init__('delete', ['del'], 'delete')


@command
class HeadersCommand(Command):
    """shows the current list of headers."""

    def __init__(self) -> None:
        super().__init__('headers', ['hs'], category=Category.HOSTS)

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if not env.host:
            return ErrorString("no host.  try 'help host'")
        if len(args) == 0:
            return StringValue(self._get_headers(env))
        else:
            return ErrorString('headers has no arguments')

    def _format_header(self, key: str, value: str) -> str:
        return (style(bold("%s: %s" % (key, value))))

    def _get_headers(self, env: Environment) -> str:
        if env.host:
            return '\n'.join(
                    self._format_header(key, env.host.headers[key])
                    for key in sorted(env.host.headers.keys()))
        else:
            return ''


@command
class HostsCommand(Command):
    """shows the current list of hosts.

    The current host is prefixed by an asterisk."""

    def __init__(self) -> None:
        super().__init__('hosts', [], category=Category.HOSTS)

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if len(args) == 0:
            return StringValue(self._get_hosts(env))
        else:
            return ErrorString('hosts has no arguments')

    def _format_host(self, var_name: str, host: Host,
                     current_host: Host) -> str:
        return ("%s%s: %s" % ('*' if host == current_host else ' ',
                              style(bold(var_name)), host.hostname))

    def _get_hosts(self, env: Environment) -> str:
        return '\n'.join(
                self._format_host(
                    var_name, cast(Host, env.lookup(
                        var_name, Host.TYPE)),
                    env.host)
                for var_name in sorted(env.variables.keys())
                if env.variables[var_name].type() == Host.TYPE)


@command
class HeaderCommand(Command):
    """sets or displays the value of a header.

For example:

    -> header
    -> header accept application/json
    """

    def __init__(self) -> None:
        super().__init__('header', ['hd'], category=Category.HOSTS)

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if not env.host:
            return ErrorString("no host.  try 'help host'")
        if len(args) == 1:
            try:
                return StringValue(env.host.headers[args[0]])
            except Exception:
                return ErrorString("unknown header: %s" % args[0])
        elif len(args) > 1:
            env.host.add_header(args[0], ' '.join(args[1:]))
            return NullValue()
        else:
            return ErrorString('usage: header NAME [VALUE]')


@command
class TypeCommand(Command):
    """displays the type of a variable."""

    def __init__(self) -> None:
        super().__init__('type', ['t'], category=Category.ENVIRONMENT)

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if args:
            try:
                return StringValue(env.lookup(args[0]).type())
            except KeyError:
                return ErrorString("unknown variable: %s" % args[0])
        else:
            return ErrorString("usage: type VAR")


@command
class EnvCommand(Command):
    """displays the environment."""

    def __init__(self) -> None:
        super().__init__('env', [], category=Category.ENVIRONMENT)

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        result = ''
        # Add host
        result += style(bold('host: '))
        if env.host:
            result += '%s (%s)' % (env.host.hostname, env.host.alias)
        # Add headers
        if env.host:
            for h, v in env.host.headers.items():
                result += '\n  %s: %s' % (style(bold(h)), v)
        result += '\n'
        # Add variables
        result += style(bold('variables:'))
        for key, val in env.variables.items():
            result += "\n  %s = %s { %s }" % (
                style(bold(key)), env.variables[key].type(),
                env.variables[key].summary())
        return StringValue(result)


@command
class RemoveCommand(Command):
    """removes a variable from the environment or a header from the current
host.

For example:

    removes a variable named host1 if it exists.
    -> rm host1

    removes the misspelled Acccept header from the current host.
    -> rm Acccept
    """

    def __init__(self) -> None:
        super().__init__('remove', ['rm', 'del'],
                         category=Category.ENVIRONMENT)

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if len(args) != 1:
            return ErrorString('usage remove NAME')
        name = args[0]
        result = env.unbind(name)
        if result:
            if result == env.host:
                env.host = None
            return StringValue('removed variable: %s' % name)
        if env.host:
            if env.host.remove_header(name):
                return StringValue('removed header: %s' % name)
        return ErrorString('no variable or header named: %s' % name)


@command
class VarsCommand(Command):
    """displays the variables in the environment.

Aliased as ls.

For example:

    -> vars
    -> ls
    """

    def __init__(self, name: str = 'vars', aliases: Sequence[str] = None,
            var_type: str=None, category: str=Category.ENVIRONMENT) -> None:
        super().__init__(name, aliases or ['ls'],
                         category=category)
        self.var_type = var_type

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        result = ''
        result += '\n'.join(
            ["%s = %s { %s }" % (
                style(bold(name)),
                env.variables[name].type(),
                env.variables[name].summary())
                for name in sorted(env.variables.keys())
                if env.lookup(name).type() == self.var_type])
        return StringValue(result)


@command
class RequestsCommand(VarsCommand):
    """displays the requests that have been defined.

For example:

    -> requests
    """

    def __init__(self) -> None:
        super().__init__('requests', [],
                         category=Category.REQUESTS, var_type=Request.TYPE)


@command
class HostCommand(Command):
    """displays or sets the current host.

For example:

    show the current host.
    -> host

    create a host with the schema specified
    -> host NAME https://api.coffeeshop.com

    create a host with no schema (you will be prompted for a schema).
    -> host NAME api.barbershop.com

    switch to a host by name
    -> host NAME
    """

    def __init__(self) -> None:
        super().__init__('host', ['h'], category=Category.HOSTS)

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        try:
            if len(args) == 1:
                # select named Host
                env.host = cast(Host,
                                env.lookup(args[0], Host.TYPE))
                return env.host
            elif len(args) == 2:
                # define new Host
                env.host = cast(
                        Host, env.bind(
                            args[0],
                            Host(args[0], self._get_host(args[1]))))
                return env.host
            elif not args and env.host:
                return env.host
            else:
                return ErrorString("no current host. try: host HOSTNAME")
        except ValueError as ex:
            raise ValueError('could not switch to host: %s' % ex)

    def _get_host(self, host: str) -> str:
        if not host.startswith('http'):
            completer = WordCompleter(['http', 'https'])
            text = prompt('Enter Schema [http/HTTPS]: ',
                          completer=completer) or 'https'
            return text + '://' + host
        return host


def read_command(line) -> Tuple[Command, Sequence[str]]:
    """Reads a command from a string and returns it."""
    if line.strip():
        return find_command(line.split(' '))
    else:
        return None, line


def evaluate_command(command, input: IO, args: Sequence[str],
                     env: Environment,
                     value: Optional[Value] = None) -> Value:
    """evaluates a command in an environment."""
    return command.evaluate(input, args, env, value=value)


def print_command_result(result: Value) -> None:
    result.display()


def get_prompt_string(env: Environment) -> str:
    if env.host:
        return '[%s] -> ' % env.host.alias
    else:
        return '-> '


def read_eval_print(input: IO, env: Environment) -> Tuple[bool, str]:
    """Reads from input until it receives a line of text.  When a line has been
    received, the line is evaluated as a command and then the result of the
    evaluation is printed.

    Comments begin with the '#' character."""
    line = None
    while True:
        line = input.get_command(get_prompt_string(env))
        if line is None:
            return False, None
        if line and not line.startswith('#'):
            break
    command, args = read_command(line)
    if command:
        input.display_command(command.name, args)
        result = evaluate_command(command, input, args, env)
        if result:
            print_command_result(result)
        return True, None
    else:
        return False, line


def banner():
    """returns a benner string that shows the program name, version, and a way
    to get help."""
    return """httpsh v%d.%d.%d
type help for help.""" % VERSION


def should_show_version() -> bool:
    if len(sys.argv) >= 2:
        return sys.argv[1] in ['--version', '-version']
    return False


def should_show_help() -> bool:
    if len(sys.argv) >= 2:
        return sys.argv[1] in ['--help', '-help', '-h', '/?']
    return False


def version_and_exit(console: IO, env: Environment) -> None:
    print("httpsh v%d.%d.%d" % VERSION)
    sys.exit(0)


def help_and_exit(console: IO, env: Environment) -> None:
    print_command_result(
            find_command(['help'])[0].evaluate(console, [], env))
    sys.exit(0)


def run_startup_script(env: Environment) -> None:
    startup_script = os.path.expanduser('~/.httpshrc')
    if os.path.exists(startup_script):
        commands['run'].evaluate(None, [startup_script], env)


def main() -> None:
    colorama.init()
    env = Environment(History())
    console = ConsoleIO(env)
    if should_show_version():
        version_and_exit(console, env)
    if should_show_help():
        help_and_exit(console, env)
    print(banner())
    run_startup_script(env)
    while True:
        try:
            success, result = read_eval_print(console, env)
            if not success:
                tokens = result.split(' ')
                if len(tokens) == 1:
                    env.lookup(tokens[0]).display()
                elif result:
                    print('unknown command or variable: %s' % result)
        except KeyboardInterrupt:
            print("Press Ctrl-D to quit.")
        except KeyError as ex:
            print(ErrorString(ex.args[0]))
        except ValueError as ex:
            print(ErrorString(ex.args[0]))
        except EOFError:
            break
        except requests.exceptions.ConnectionError as ex:
            print(ErrorString(str(ex)))
        except:
            # print some information about unexpected errors.
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
