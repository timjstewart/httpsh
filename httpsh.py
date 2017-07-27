import datetime
import json
import os
import re
import requests
import statistics
import sys

from abc import ABC, abstractmethod

import colorama

from prompt_toolkit.shortcuts import prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.contrib.completers import WordCompleter


commands = {}


def pretty_json(d):
    """returns a pretty rendition of a dictionary."""
    return json.dumps(d, sort_keys=True, indent=4)


def color(s):
    return str(s) + colorama.Style.RESET_ALL


def bold(s):
    return colorama.Style.BRIGHT + str(s)


def blue(s):
    return colorama.Fore.BLUE + str(s)


def red(s):
    return colorama.Fore.RED + str(s)


def green(s):
    return colorama.Fore.GREEN + str(s)


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


class Environment(object):
    """The shell's environment."""

    def __init__(self, history):
        self.host = None
        self.history = history
        self.variables = {}


class History(object):
    """Group all history objects together."""

    COMMAND_HISTORY_FILE = "~/.httpsh_history"
    PAYLOAD_HISTORY_FILE = "~/.httpsh_payload_history"

    def __init__(self):
        self.command_history = FileHistory(
                os.path.expanduser(History.COMMAND_HISTORY_FILE))
        self.payload_history = FileHistory(
                os.path.expanduser(History.PAYLOAD_HISTORY_FILE))


class Value(ABC):

    @abstractmethod
    def display(self):
        pass

    @abstractmethod
    def summary(self):
        pass

    @abstractmethod
    def type(self):
        pass


class HostValue(Value):

    TYPE = 'host'

    def __init__(self, hostname):
        self.hostname = hostname
        self.headers = {}

    def display(self):
        print(color(bold(self.hostname)))
        for header in sorted(self.headers.keys()):
            print('  %s: %s' % (color(bold(header)), self.headers[header]))

    def summary(self):
        return "hostname = %s" % self.hostname

    def type(self):
        return HostValue.TYPE


class NullValue(Value):

    def __init__(self):
        pass

    def display(self):
        pass

    def summary(self):
        return "null"

    def type(self):
        return None


class StringValue(Value):

    def __init__(self, text):
        self.text = text.strip()

    def summary(self):
        return self.text[:20] + ('...' if len(self.text) > 20 else '')

    def display(self):
        if self.text:
            print(color(bold(self.text)))

    def type(self):
        return 'text'


class Response(Value):

    TYPE = 'response'

    def __init__(self, resp):
        self.resp = resp
        self.elapsed = None

    def summary(self):
        return "status: %d, length: %d" % (
                self.resp.status_code,
                len(self.resp.content))

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
        sys.stdout.write("%s bytes" % color(bold(len(resp.content))))

    def _print_elapsed_time(self):
        sys.stdout.write("%s seconds" %
                         color(bold("%.3f" % self.elapsed.total_seconds())))

    def _print_resp_headers(self):
        for key in sorted(self.resp.headers.keys()):
            print("%s: %s" %
                  (color(bold(key)), self.resp.headers[key]))

    def _print_status_code(self):
        if self.resp.status_code >= 400:
            status_code = color(red(bold(self.resp.status_code)))
        else:
            status_code = color(green(bold(self.resp.status_code)))
        sys.stdout.write("%s" % status_code)

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


class IO(ABC):

    @abstractmethod
    def get_command(self, prompt_text):
        pass

    @abstractmethod
    def get_payload(self, prompt_text):
        pass

    @abstractmethod
    def display_command(self, command, args):
        pass


class ConsoleIO(IO):

    def __init__(self, history):
        self.history = history

    def get_payload(self, prompt_text):
        return prompt(
                prompt_text,
                auto_suggest=AutoSuggestFromHistory(),
                history=self.history.payload_history).strip()

    def get_command(self, prompt_text):
        return prompt(
                prompt_text,
                auto_suggest=AutoSuggestFromHistory(),
                history=self.history.command_history).strip()

    def display_command(self, command, args):
        pass


class FileIO(IO):
    """Reads commands from a file."""

    def __init__(self, file):
        self.file = file

    def get_command(self, prompt_text):
        return self.file.readline().strip()

    def get_payload(self, prompt_text):
        return self.file.readline().strip()

    def display_command(self, command, args):
        print("%s>> %s %s%s" % (
            colorama.Fore.BLUE, command.name,
            ' '.join(args), colorama.Fore.RESET))


class Command(ABC):

    def __init__(self, name, aliases):
        self.name = name
        self.aliases = aliases

    @abstractmethod
    def evaluate(self, input, arguments, environment):
        pass


@command
class HelpCommand(Command):
    """displays help screen."""

    def __init__(self):
        super().__init__('help', ['?'])

    def evaluate(self, input, arguments, environment):
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

    def _format_doc_string_short(self, command):
            return "%-7s - %s" % (
                    command.name, command.__doc__.split('\n')[0])

    def _format_doc_string_long(self, command):
            return "%s - %s" % (command.name, command.__doc__)


@command
class LoadCommand(Command):
    """loads a file and evaluates commands from it.

For example:

    -> load script
    -> load ~/script
    """

    def __init__(self):
        super().__init__('load', [])

    def evaluate(self, _, arguments, environment):
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
                print("Script ran in: %s%.3f%s seconds" % (
                    colorama.Style.BRIGHT, elapsed.total_seconds(),
                    colorama.Style.NORMAL))


class AssignCommand(Command):

    def __init__(self, var_name, command):
        super().__init__(command.name, [])
        self.var_name = var_name
        self.command = command

    def evaluate(self, input, arguments, env):
        result = self.command.evaluate(input, arguments, env)
        env.variables[self.var_name] = result
        return NullValue()


@command
class RepeatCommand(Command):
    """repeats a command n times and prints average time.

There is no delay between repetitions.

For example:

    -> repeat 100 get /customers
    -> repeat 10 load script
    """

    def __init__(self):
        super().__init__('repeat', [])

    def evaluate(self, input, arguments, env):
        repetitions = int(arguments[0])
        command, cmd_args = find_command(arguments[1:])
        if command:
            times = []
            for _ in range(0, repetitions):
                start = datetime.datetime.now()
                result = command.evaluate(input, cmd_args, env)
                print_command_result(result)
                times.append((datetime.datetime.now() - start)
                             .total_seconds())
            return StringValue(
                    'Ran command: %d times.  Average time: %f seconds' % (
                        repetitions, statistics.mean(times)))


@command
class SelectCommand(Command):
    """selects a sub-tree of a JSON response.

For example:

    -> select resp
    -> select resp content
    -> select resp content._links.href
    -> select resp content.*Name
    """

    def __init__(self):
        super().__init__('select', [])

    def evaluate(self, input, arguments, env):
        if arguments:
            # find the variable we're running select on and make sure it's
            # a Response.  You can't run select on a Host.
            if (arguments[0] in env.variables and
                    env.variables[arguments[0]].type() == Response.TYPE):
                resp = env.variables[arguments[0]]
                # make sure that the Response contains JSON.  You can't run
                # select on an HTML or XML document.
                if resp.is_json():
                    if len(arguments) == 2:
                        return self._select(resp, arguments[1])
                    else:
                        return resp
                else:
                    return StringValue("response is not JSON.")
            else:
                return StringValue("no HTTP response by that name: %s" %
                                   arguments[0])
        else:
            return StringValue("usage select RESPONSE [SELECT_STATEMENT]")

    def _select(self, resp, select_stmt):
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

    def _matches(self, pattern, string):
        """determines if the pattern from the select statement matches a
        particular string.  The patterns currently only support one special
        character, the asterisk, which acts like a '.*' in the language of
        regular expressions."""
        regex_pattern = pattern.replace('*', '.*')
        return re.match(regex_pattern, string)

    def _get_matching_keys(self, node, pattern):
        """returns all keys in a dictionary that match the given pattern."""
        if type(node) == dict:
            return [key for key in node.keys()
                    if self._matches(pattern, key)]

    def _append(self, results, item):
        if type(item) == list:
            results.extend(item)
        else:
            results.append(item)

    def _parse_expression(self, expression):
        m = re.match('([^(]*)(\\(([^)]*)\\))?', expression)
        patterns = [pattern for pattern in m.group(1).split(',')
                    if pattern.strip()]
        collect = ([item for item in m.group(3).split(',') if item.strip()]
                   if m.group(3) else [])
        return patterns, collect

    def _merge_dicts(self, collected, selected):
            collected.update(selected)
            return collected

    def _select_part(self, node, part, parts, collect_here, collected):
        patterns, collect = self._parse_expression(part)
        if type(node) == dict:
            collected.update(
                    {key: node[key]
                     for c in collect_here
                     for key in self._get_matching_keys(node, c)})
            if parts:
                return {key: self._select_part(
                        node[key], parts[0], parts[1:], collect, collected)
                        for pattern in patterns
                        for key in self._get_matching_keys(node, pattern)}
            else:
                return self._merge_dicts(
                        collected,
                        {key: node[key]
                         for pattern in patterns
                         for key in self._get_matching_keys(node, pattern)})
        elif type(node) == list:
            return [self._select_part(item, pattern,
                                      parts, collect_here, collected)
                    for pattern in patterns
                    for item in node]
        elif not parts and patterns == ['*']:
            return node
        else:
            # There are still more expressions in parts but we have navigated
            # as deeply into the JSON as possible.  The selection has failed.
            return None


class HttpCommand(Command):

    def __init__(self, name, aliases, method):
        super().__init__(name, aliases)
        self.method = method

    def evaluate(self, input, arguments, env):
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
            return resp
        else:
            return StringValue('please specify a host.')

    def get_payload(self, input, env):
        return None


@command
class HeadCommand(HttpCommand):
    """sends a HEAD request using the current value of host and headers.

For example:

    -> head /customers
    """

    def __init__(self):
        super().__init__('head', ['HEAD'], 'head')


@command
class OptionsCommand(HttpCommand):
    """sends a OPTIONS request using the current value of host and headers.

For example:

    -> options /customers
    """

    def __init__(self):
        super().__init__('options', ['OPTIONS', 'opt'], 'options')


@command
class GetCommand(HttpCommand):
    """sends a GET request using the current value of host and headers.

For example:

    -> get /customers
    """

    def __init__(self):
        super().__init__('get', ['GET', 'g'], 'get')


class PayloadCommand(HttpCommand):

    def __init__(self, name, aliases, method):
        super().__init__(name, aliases, method)

    def get_payload(self, input, env):
        payload = input.get_payload('Enter Payload: ')
        json_payload = json.loads(payload)
        return json_payload


@command
class PutCommand(PayloadCommand):
    """sends a PUT request using the current value of host and headers.

For example:

    -> put /customers
    """

    def __init__(self):
        super().__init__('put', ['PUT', 'pu'], 'put')


@command
class PostCommand(PayloadCommand):
    """sends a POST request using the current value of host and headers.

For example:

    -> post /customers
    """

    def __init__(self):
        super().__init__('post', ['POST', 'po'], 'post')


@command
class GetWithPayloadCommand(PayloadCommand):
    """sends a GET request with a payload.

For example:

    -> getp /customers

Note: If you're wondering who would ever have a service where a GET request
sent a payload, check out ElasticSearch.
    """

    def __init__(self):
        super().__init__('getp', ['GETP', 'gp'], 'get')


@command
class PatchCommand(PayloadCommand):
    """sends a PATCH request using the current value of host and headers.

For example:

    -> patch /customers
    """

    def __init__(self):
        super().__init__('patch', ['pat'], 'patch')


@command
class DeleteCommand(HttpCommand):
    """sends a DELETE request using the current value of host and headers.

For example:

    -> delete /customers
    """

    def __init__(self):
        super().__init__('delete', ['del'], 'delete')


@command
class HeadersCommand(Command):
    """shows the current list of headers."""

    def __init__(self):
        super().__init__('headers', ['hs'])

    def evaluate(self, input, args, env):
        if not env.host:
            return StringValue('no host defined')
        if len(args) == 0:
            return StringValue(self._get_headers(env))
        else:
            return StringValue('headers has no arguments')

    def _format_header(self, key, value):
        return ("%s%s: %s%s" % (
            colorama.Style.BRIGHT, key, value, colorama.Style.NORMAL))

    def _get_headers(self, env):
        if env.host:
            return '\n'.join(
                    self._format_header(key, env.host.headers[key])
                    for key in sorted(env.host.headers.keys()))
        else:
            return ''


@command
class HostsCommand(Command):
    """shows the current list of hosts."""

    def __init__(self):
        super().__init__('hosts', [])

    def evaluate(self, input, args, env):
        if len(args) == 0:
            return StringValue(self._get_hosts(env))
        else:
            return StringValue('hosts has no arguments')

    def _format_host(self, var_name, host):
        return ("%s: %s" % (var_name, host.hostname))

    def _get_hosts(self, env):
        if env.host:
            return '\n'.join(
                    self._format_host(var_name, env.variables[var_name])
                    for var_name in sorted(env.variables.keys())
                    if env.variables[var_name].type() == HostValue.TYPE)
        else:
            return ''


@command
class HeaderCommand(Command):
    """sets or displays the value of a header.

For example:

    -> header
    -> header accept application/json
    """

    def __init__(self):
        super().__init__('header', ['hd'])

    def evaluate(self, input, args, env):
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

    def __init__(self):
        super().__init__('type', ['t'])

    def evaluate(self, input, args, env):
        if args[0] in env.variables:
            return StringValue(env.variables[args[0]].type())
        else:
            return StringValue("unknown variable: %s" % args[0])


@command
class EnvCommand(Command):
    """displays the environment."""

    def __init__(self):
        super().__init__('env', [])

    def evaluate(self, input, args, env):
        result = ''
        result += 'host: %s\n' % (env.host.hostname if env.host else '')
        result += 'headers:\n%s' % (''.join('  %s: %s\n' % (k, v) for k, v
                                    in env.host.headers.items())
                                    if env.host else '')
        result += 'variables:\n%s' % ('\n'.join(
            ("  %s = %s { %s }" % (
                name,
                env.variables[name].type(),
                env.variables[name].summary())
                for name in env.variables.keys())))
        return StringValue(result)


@command
class RemoveCommand(Command):
    """removes a variable from the environment.

For example:

    -> rm host1
    """

    def __init__(self):
        super().__init__('remove', ['rm'])

    def evaluate(self, input, args, env):
        if len(args) != 1:
            return StringValue('usage remove VARNAME')
        result = env.variables.pop(args[0], None)
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

    def __init__(self):
        super().__init__('vars', ['ls'])

    def evaluate(self, input, args, env):
        result = ''
        result += '\n'.join(
            ("%s = %s { %s }" % (
                name,
                env.variables[name].type(),
                env.variables[name].summary())
                for name in env.variables.keys()))
        return StringValue(result)


@command
class HostCommand(Command):
    """displays or sets the current host.

For example:

    -> host
    -> host https://api.coffeeshop.com
    -> host api.barbershop.com
    """

    def __init__(self):
        super().__init__('host', ['h'])

    def evaluate(self, input, args, env):
        if len(args) == 1:
            if (args[0] in env.variables and
                    env.variables[args[0]].type() == HostValue.TYPE):
                env.host = env.variables[args[0]]
            else:
                env.host = HostValue(self._get_host(args[0]))
            return env.host
        elif env.host:
            return env.host
        else:
            return StringValue("no host.  Try: host HOSTNAME")

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


def evaluate_command(command, input, args, env):
    """evaluates a command in an environment."""
    return command.evaluate(input, args, env)


def print_command_result(result):
    result.display()


def read_eval_print(input, env):
    line = input.get_command('-> ')
    command, args = read_command(line)
    if command:
        input.display_command(command, args)
        result = evaluate_command(command, input, args, env)
        if result:
            print_command_result(result)
        return True, result
    else:
        return False, line


def main():
    colorama.init()
    env = Environment(History())
    console = ConsoleIO(env.history)
    while True:
        try:
            success, result = read_eval_print(console, env)
            if not success:
                tokens = result.split(' ')
                if len(tokens) == 1 and tokens[0] in env.variables:
                    env.variables[tokens[0]].display()
                elif result:
                    print('unknown command or variable: %s' % result)
        except KeyboardInterrupt:
            print("Press Ctrl-D to quit.")
        except EOFError:
            break
        except:
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
