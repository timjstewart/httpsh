import datetime
import json
import os
import re
import requests
import statistics
import sys

from abc import ABC, abstractmethod
from typing import (Optional, Dict, Tuple, Sequence, Any, Mapping, List, Union,
                    cast)
from typing import TextIO

import colorama

from prompt_toolkit.shortcuts import prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.contrib.completers import WordCompleter
from pygments import highlight, lexers, formatters


VERSION = (0, 0, 1)

JsonValue = Union[int, bool, float, List[Any], Dict[str, Any]]


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


ValueOrStr = Union[str, Value]


class History(object):
    """Group all history objects together."""

    COMMAND_HISTORY_FILE = "~/.httpsh_history"
    PAYLOAD_HISTORY_FILE = "~/.httpsh_payload_history"

    def __init__(self) -> None:
        self.command_history = FileHistory(
                os.path.expanduser(History.COMMAND_HISTORY_FILE))
        self.payload_history = FileHistory(
                os.path.expanduser(History.PAYLOAD_HISTORY_FILE))


class HostValue(Value):

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
        return HostValue.TYPE


class Environment(object):
    """The shell's environment."""

    def __init__(self, history: History) -> None:
        self.host = None  # type: HostValue
        self.history = history
        self.variables = {}  # type: Dict[str, Value]

    def bind(self, var_name: str, value: Value) -> Value:
        if value:
            self.variables[var_name] = value
        else:
            self.variables.pop(var_name, None)
        return value

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
    def get_payload(self, prompt_text: str) -> str:
        pass

    @abstractmethod
    def display_command(self, command: str, args: Sequence[str]) -> None:
        pass


class Command(ABC):

    def __init__(self, name: str, aliases: Sequence[str]) -> None:
        self.name = name
        self.aliases = aliases

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

    def get_payload(self, prompt_text: str) -> str:
        return prompt(
                prompt_text,
                auto_suggest=AutoSuggestFromHistory(),
                history=self.env.history.payload_history).strip()

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

    def get_payload(self, prompt_text: str) -> str:
        line = self.file.readline()
        return line.strip() if line else None

    def display_command(self, command: str, args: Sequence[str]) -> None:
        print("%s>> %s %s%s" % (
            colorama.Fore.BLUE, command,
            ' '.join(args), colorama.Fore.RESET))


commands = {}  # type: Dict[str, Command]


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


def command(c):
    """Decorator that creates an instance of the decorated Command class and
    registers it under its name and aliases."""
    instance = c()
    commands[instance.name] = instance
    for alias in instance.aliases:
        commands[alias] = instance


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

    def __init__(self, text: str) -> None:
        self.text = text.strip()

    def summary(self) -> str:
        return self.text[:20] + ('...' if len(self.text) > 20 else '')

    def display(self) -> None:
        if self.text:
            print(self.text)

    def type(self) -> str:
        return 'text'


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
        rvalue, args2 = find_command(tokens[2:])
        return AssignCommand(var_name, rvalue), args2
    elif tokens[0] in commands:
        return commands[tokens[0]], tokens[1:]
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
            for command in sorted(set(commands.values()),
                                  key=lambda x: x.name):
                print(" %s" % self._format_doc_string_short(command))

    def _format_doc_string_short(self, command: Command) -> str:
        return "%-7s - %s" % (
                command.name, command.__doc__.split('\n')[0])

    def _format_doc_string_long(self, command: Command) -> str:
        doc = "%s - %s" % (command.name, command.__doc__)
        if command.aliases:
            doc += '\nAliases: %s' % ', '.join(command.aliases)
        return doc


@command
class LoadCommand(Command):
    """loads a file and evaluates commands from it.

For example:

    -> load script
    -> load ~/script
    """

    def __init__(self) -> None:
        super().__init__('load', ['.', 'source', 'run'])

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
                return StringValue("Script ran in: %s%.3f%s seconds" % (
                    colorama.Style.BRIGHT, elapsed.total_seconds(),
                    colorama.Style.NORMAL))
        else:
            raise ValueError('usage: load SCRIPT_NAME')


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
            raise ValueError('%s commands are not assignable' %
                             self.command.name)


@command
class RepeatCommand(Command):
    """repeats a command n times and prints average time.

There is no delay between repetitions.

For example:

    -> repeat 100 get /customers
    -> repeat 10 load script
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
                        repetitions, statistics.mean(times)))
        else:
            raise KeyError('unknown command: %s' % arguments)


@command
class SelectCommand(Command):
    """selects a sub-tree of a JSON response.

For example:

    display all of the JSON in the response.
    -> select resp

    display only the content node of the JSON response.
    -> select resp content

    display only the href node of each item in content._links.
    -> select resp content._links.href

    display all properties ending in Name
    -> select resp content.*Name

    display only the firstName and lastName properties
    -> select resp content.firstName,lastName

    display all properties ending in Name and include the value
    of content's totalPages property.
    -> select resp content(totalPages).*Name

    display all properties ending in Name and include the value
    of the root JSON node's totalPages property.
    -> select resp (totalPages).*Name
    """

    def __init__(self) -> None:
        super().__init__('select', [])

    def _get_resp(self, arguments: Sequence[Any],
                  value: Optional[Value],
                  env: Environment) -> Tuple[Response, Sequence[str]]:
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
                return StringValue("response is not JSON.")
        else:
            return StringValue("usage select RESPONSE [SELECT_STATEMENT]")

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
        regex_pattern = pattern.replace('*', '.*')
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
        super().__init__(name, aliases)
        self.method = method

    def is_assignable(self) -> bool:
        return True

    def evaluate(self, input: IO, arguments: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if env.host:
            path = (arguments[0] if arguments else '/')
            path = ('/' + path) if not path.startswith('/') else path
            start = datetime.datetime.now()
            resp = Response(requests.request(
                    self.method,
                    env.host.hostname + path,
                    headers=env.host.headers,
                    json=self.get_payload(input, env)))
            resp.elapsed = datetime.datetime.now() - start

            if len(arguments) > 2 and arguments[1] == '|':
                # filter the HTTP response through a select statement
                select_stmt = arguments[2]
                return commands['select'].evaluate(
                        input, [select_stmt], env, value=resp)
            else:
                return resp
        else:
            return StringValue('please specify a host.')

    def get_payload(self, input: IO, env: Environment) -> str:
        return None


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

    def get_payload(self, input: IO, env: Environment) -> str:
        payload = input.get_payload('Enter Payload: ')
        json_payload = json.loads(payload)
        return json_payload


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
        super().__init__('headers', ['hs'])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if not env.host:
            return StringValue('no host defined')
        if len(args) == 0:
            return StringValue(self._get_headers(env))
        else:
            return StringValue('headers has no arguments')

    def _format_header(self, key: str, value: str) -> str:
        return ("%s%s: %s%s" % (
            colorama.Style.BRIGHT, key, value, colorama.Style.NORMAL))

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
        super().__init__('hosts', [])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if len(args) == 0:
            return StringValue(self._get_hosts(env))
        else:
            return StringValue('hosts has no arguments')

    def _format_host(self, var_name: str, host: HostValue,
                     current_host: HostValue) -> str:
        return ("%s%s: %s" % ('*' if host == current_host else ' ',
                              var_name, host.hostname))

    def _get_hosts(self, env: Environment) -> str:
        return '\n'.join(
                self._format_host(
                    var_name, cast(HostValue, env.lookup(
                        var_name, HostValue.TYPE)),
                    env.host)
                for var_name in sorted(env.variables.keys())
                if env.variables[var_name].type() == HostValue.TYPE)


@command
class HeaderCommand(Command):
    """sets or displays the value of a header.

For example:

    -> header
    -> header accept application/json
    """

    def __init__(self) -> None:
        super().__init__('header', ['hd'])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if not env.host:
            return StringValue('no host defined')
        if len(args) == 1:
            try:
                return StringValue(env.host.headers[args[0]])
            except Exception:
                return StringValue("unknown header: %s" % args[0])
        elif len(args) > 1:
            env.host.headers[args[0]] = ' '.join(args[1:])
            return NullValue()
        else:
            return StringValue('usage: header NAME [VALUE]')


@command
class TypeCommand(Command):
    """displays the type of a variable."""

    def __init__(self) -> None:
        super().__init__('type', ['t'])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if args:
            try:
                return StringValue(env.lookup(args[0]).type())
            except KeyError:
                return StringValue("unknown variable: %s" % args[0])
        else:
            return StringValue("usage: type VAR")


@command
class EnvCommand(Command):
    """displays the environment."""

    def __init__(self) -> None:
        super().__init__('env', [])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        result = ''
        # Add host
        result += bold('host: ')
        if env.host:
            result += '%s (%s)' % (env.host.hostname, env.host.alias)
        result += '\n'
        # Add headers
        result += bold('headers:')
        if env.host:
            for h, v in env.host.headers.items():
                result += '\n  %s: %s' % (h, v)
        result += '\n'
        # Add variables
        result += bold('variables:')
        for key, val in env.variables.items():
            result += "\n  %s = %s { %s }" % (
                key, env.variables[key].type(),
                env.variables[key].summary())
        return StringValue(result)


@command
class RemoveCommand(Command):
    """removes a variable from the environment.

For example:

    -> rm host1
    """

    def __init__(self) -> None:
        super().__init__('remove', ['rm'])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        if len(args) != 1:
            return StringValue('usage remove VARNAME')
        result = env.variables.pop(args[0], None)
        if result == env.host:
            env.host = None
        if not result:
            return StringValue('no variable named: %s' % args[0])
        return NullValue()


@command
class VarsCommand(Command):
    """displays the variables in the environment.

Aliased as ls.

For example:

    -> vars
    -> ls
    """

    def __init__(self) -> None:
        super().__init__('vars', ['ls'])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        result = ''
        result += '\n'.join(
            ["%s = %s { %s }" % (
                name,
                env.variables[name].type(),
                env.variables[name].summary())
                for name in sorted(env.variables.keys())])
        return StringValue(result)


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
        super().__init__('host', ['h'])

    def evaluate(self, input: IO, args: Sequence[str],
                 env: Environment,
                 value: Optional[Value] = None) -> Value:
        try:
            if len(args) == 1:
                # select named Host
                env.host = cast(HostValue,
                                env.lookup(args[0], HostValue.TYPE))
                return env.host
            elif len(args) == 2:
                # define new Host
                env.host = cast(
                        HostValue, env.bind(
                            args[0],
                            HostValue(args[0], self._get_host(args[1]))))
                return env.host
            elif not args and env.host:
                return env.host
            else:
                return StringValue("no current host. try: host HOSTNAME")
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


def main() -> None:
    colorama.init()
    env = Environment(History())
    console = ConsoleIO(env)
    print(banner())
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
            print(ex.args[0])
        except ValueError as ex:
            print(ex.args[0])
        except EOFError:
            break
        except:
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
