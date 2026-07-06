import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Argument:
    name: str
    dest: str | None = None
    type: Callable[[str], Any] | None = None
    help: str | None = None
    default: Any | None = None
    required: bool = False

@dataclass
class LeafCommand:
    main_func: Callable[[argparse.Namespace], None]
    arguments: list[Argument] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    help: str | None = None

@dataclass
class Command:
    subcommands: dict[str, Command | LeafCommand]
    arguments: list[Argument] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    help: str | None = None

def make_parser(name: str, cmd: Command | LeafCommand, parent_subparsers=None) -> argparse.ArgumentParser:
    if parent_subparsers is not None:
        parser = parent_subparsers.add_parser(name, help=cmd.help)
    else:
        parser = argparse.ArgumentParser(prog=name)

    for argument in cmd.arguments:
        parser.add_argument(argument.name, type=argument.type, help=argument.help, required=argument.required, default=argument.default)

    parser.set_defaults(**cmd.defaults)

    if isinstance(cmd, LeafCommand):
        parser.set_defaults(main_func=cmd.main_func)

    if isinstance(cmd, Command):
        subparsers = parser.add_subparsers(dest=f"__{name}_subparsers")
        subparsers.required = True
        for name, subcommand in cmd.subcommands.items():
            make_parser(name, subcommand, subparsers)

    return parser
