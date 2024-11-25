import signal
import os
import sys
import re
import shlex
import json
from parsing import split_argument, split_by_pipe_op

# DO NOT REMOVE THIS FUNCTION!
# This function is required in order to correctly switch the terminal foreground group to
# that of a child process.
def setup_signals() -> None:
    """
    Setup signals required by this program.
    """
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)

def initialise():
    mysh_path = os.path.join(os.environ.get("MYSHDOTDIR", os.path.expanduser("~")), ".myshrc")
    if "PATH" not in os.environ:
        os.environ["PATH"] = "/bin:/usr/bin:/usr/local/bin:/sbin:/usr/sbin:/usr/local/sbin"
    if not os.path.exists(mysh_path):
        os.environ.setdefault("PROMPT", ">> ")
        os.environ.setdefault("MYSH_VERSION", "1.0")
        return
    try:
        with open(mysh_path, "r") as f1:
            if os.path.getsize(mysh_path) == 0:
                print("mysh: .myshrc is empty, no configurations loaded", file=sys.stderr)
                return
            config = json.load(f1)
    except json.JSONDecodeError:
        print("mysh: invalid JSON format for .myshrc", file=sys.stderr)
        return
    for key, value in config.items():
        if not isinstance(key, str):
            print(f"mysh: .myshrc: invalid key type: {key} (keys must be strings)", file=sys.stderr)
            continue
        if not isinstance(value, str):
            print(f"mysh: .myshrc: {key}: not a string", file=sys.stderr)
            continue
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
            print(f"mysh: .myshrc: {key}: invalid characters for variable name", file=sys.stderr)
            continue
        value = os.path.expandvars(value)
        os.environ[key] = value
    os.environ.setdefault("PROMPT", ">> ")
    os.environ.setdefault("MYSH_VERSION", "1.0")

def handle_exit(args):
    if len(args) > 1:
        print(f"exit: too many arguments", file=sys.stderr)
    elif len(args) == 1:
        try:
            exit_code = int(args[0])
            sys.exit(exit_code)
        except ValueError:
            print(f"exit: non-integer exit code provided: {args[0]}", file=sys.stderr)
    else:
        sys.exit(0)

def handle_pwd(args):
    valid_options = {'P'}
    resolve_symlinks = False
    for arg in args:
        if arg.startswith('-'):
            for char in arg[1:]:
                if char not in valid_options:
                    print(f"pwd: invalid option: -{char}", file=sys.stderr)
                    return
            if 'P' in arg:
                resolve_symlinks = True
        else:
            print(f"pwd: invalid option: {arg}", file=sys.stderr)
            return
    if resolve_symlinks:
        cwd = os.path.realpath(os.getcwd())
    else:
        cwd = os.environ.get('PWD', os.getcwd())
    print(cwd)

def handle_cd(args):
    if len(args) > 1:
        print("cd: too many arguments", file=sys.stderr)
        return
    path = os.path.expanduser(args[0]) if args else os.path.expanduser("~")
    try:
        os.chdir(path)
        new_pwd = os.path.normpath(os.path.join(os.environ['PWD'], path)) if not os.path.isabs(path) else os.path.abspath(path)
        os.environ['PWD'] = new_pwd
    except FileNotFoundError:
        print(f"cd: no such file or directory: {path}", file=sys.stderr, flush=True)
    except NotADirectoryError:
        print(f"cd: not a directory: {path}", file=sys.stderr)
    except PermissionError:
        print(f"cd: permission denied: {path}", file=sys.stderr)

def handle_which(args):
    if not args:
        print("usage: which command ...", file=sys.stderr)
        return
    for cmd in args:
        found = False
        if cmd in BUILT_IN_COMMANDS:
            print(f"{cmd}: shell built-in command")
            found = True
        else:
            for path in os.environ.get("PATH", os.defpath).split(os.pathsep):
                full_path = os.path.join(path, cmd)
                if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                    print(full_path)
                    found = True
                    break
        if not found:
            print(f"{cmd} not found")

def valid_name(name):
    return re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name) is not None

def handle_var(args):
    if not args:
        print(f"var: expected 2 arguments, got {len(args)}", file=sys.stderr)
        return
    valid_flags = {'-s'}
    options = args[0]
    if options.startswith('-'):
        for char in options[1:]:
            if f"-{char}" not in valid_flags:
                print(f"var: invalid option: -{char}", file=sys.stderr)
                return
        if options == '-s':
            if len(args) != 3:
                print(f"var: expected 3 arguments with -s flag, got {len(args)}", file=sys.stderr)
                return
            var_name = args[1].lstrip('\\')
            command = os.path.expanduser(args[2])
            if var_name.startswith("${") and var_name.endswith("}"):
                var_name = var_name[2:-1]
            if not valid_name(var_name):
                print(f"mysh: syntax error: invalid characters for variable {var_name}", file=sys.stderr)
                return
            piped_commands = split_by_pipe_op(command)
            r, w = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(r)
                os.dup2(w, sys.stdout.fileno())
                os.close(w)
                handle_piped_commands(piped_commands)
                sys.exit(0)
            else:
                os.close(w)
                output = os.read(r, 4096).decode()
                os.close(r)
                os.waitpid(pid, 0)
                if command.startswith("echo ") or (os.path.isfile(command) and os.access(command, os.X_OK)):
                    os.environ[var_name] = output.rstrip('\n')
                else:
                    os.environ[var_name] = output
    else:
        if len(args) != 2:
            print(f"var: expected 2 arguments, got {len(args)}", file=sys.stderr)
            return
        var_name = args[0]
        value = os.path.expanduser(args[1])
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        else:
            value = os.path.expandvars(value)
        value = os.path.expanduser(value)
        if not valid_name(var_name):
            print(f"var: invalid characters for variable {var_name}", file=sys.stderr)
            return
        os.environ[var_name] = value

def handle_command(cmd, args):
    expanded_args = []
    for arg in args:
        if arg == '$PWD':
            expanded_arg = arg
        elif "\\" in arg:
            expanded_arg = re.sub(r'\\\$\{(\w+)\}', r'${\1}', arg)
        else:
            expanded_arg = os.path.expandvars(arg)
        invalid_vars = re.findall(r'\$\{([^}]+)\}', expanded_arg)
        expanded_arg = os.path.expanduser(expanded_arg)
        for var in invalid_vars:
            if not valid_name(var):
                print(f"mysh: syntax error: invalid characters for variable {var}", file=sys.stderr)
                return
            if var not in os.environ:
                expanded_arg = expanded_arg.replace(f"${{{var}}}", "")
        expanded_args.append(expanded_arg)
    command_path = None
    if os.path.isfile(cmd) or cmd.startswith('./'):
        command_path = cmd
    else:
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            potential_path = os.path.join(path_dir, cmd)
            if os.path.isfile(potential_path) and os.access(potential_path, os.X_OK):
                command_path = potential_path
                break
    if command_path:
        if os.access(command_path, os.X_OK):
            pid = os.fork()
            if pid == 0:
                os.setpgrp()
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                if "\\" not in cmd:
                    os.execvp(command_path, [cmd] + expanded_args)
                else:
                    var = expanded_args[0].replace(r"\${", "${")
                    os.execvp(command_path, [cmd, var])
            else:
                try:
                    os.waitpid(pid, 0)
                except KeyboardInterrupt:
                    os.killpg(os.getpgid(pid), signal.SIGINT)
                    os.waitpid(pid, 0)
        else:
            print(f"mysh: permission denied: {cmd}", file=sys.stderr)
    else:
        print(f"mysh: command not found: {cmd}", file=sys.stderr)

def handle_piped_commands(commands):
    num_commands = len(commands)
    for command in commands:
        if not command.strip():
            print("mysh: syntax error: expected command after pipe", file=sys.stderr)
            return
    pipes = [os.pipe() for _ in range(num_commands - 1)]
    processes = []
    for i, command in enumerate(commands):
        pid = os.fork()
        if pid == 0:
            if i > 0:
                os.dup2(pipes[i-1][0], sys.stdin.fileno())
            if i < num_commands - 1:
                os.dup2(pipes[i][1], sys.stdout.fileno())
            for pipe in pipes:
                os.close(pipe[0])
                os.close(pipe[1])
            args = split_argument(command)
            if not args:
                print("mysh: syntax error: expected command after pipe", file=sys.stderr)
                sys.exit(1)
            cmd = args.pop(0)
            if cmd in BUILT_IN_COMMANDS:
                BUILT_IN_COMMANDS[cmd](args)
                sys.exit(0)
            else:
                handle_command(cmd, args)
                sys.exit(0)
        else:
            processes.append(pid)
    for pipe in pipes:
        os.close(pipe[0])
        os.close(pipe[1])
    for pid in processes:
        os.waitpid(pid, 0)

# i have made a dictionary below storing all the built in commands in my shell
BUILT_IN_COMMANDS = {
    'exit': handle_exit,
    'pwd': handle_pwd,
    'cd': handle_cd,
    'which': handle_which,
    'var': handle_var
}

def main() -> None:
    # DO NOT REMOVE THIS FUNCTION CALL!
    setup_signals()
    initialise()
    while True:
        try:
            cmd_str = input(os.environ.get("PROMPT", ">> "))
            if not cmd_str.strip():
                continue
            piped_commands = split_by_pipe_op(cmd_str)
            if len(piped_commands) > 1:
                handle_piped_commands(piped_commands)
            else:
                args = split_argument(cmd_str)
                if not args:
                    continue
                cmd = args.pop(0)
                if cmd in BUILT_IN_COMMANDS:
                    BUILT_IN_COMMANDS[cmd](args)
                else:
                    handle_command(cmd, args)
        except EOFError:
            print()
            break


if __name__ == "__main__":
    main()