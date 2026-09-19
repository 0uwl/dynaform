"""Extract, validate, and group DynaForm variables from a Jinja2 template.

Syntax (see dynaform.md / plan.md): every undeclared template variable must
be named ``<PREFIX>_<name>`` where PREFIX is one of S/P/N/B/R (text /
password / number / checkbox / radio). Radio variables are further named
``R_<group>_<option>`` and are grouped into one radio-button set per group.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from jinja2 import TemplateSyntaxError, nodes
from jinja2.meta import find_undeclared_variables
from jinja2.sandbox import SandboxedEnvironment

_ENV = SandboxedEnvironment()

_NAME_RE = re.compile(r"^(?P<prefix>[SPNBR])_(?P<rest>[A-Za-z][A-Za-z0-9_]*)$")
_RADIO_RE = re.compile(r"^(?P<group>[A-Za-z0-9]+)_(?P<option>[A-Za-z][A-Za-z0-9_]*)$")


class TemplateValidationError(Exception):
    """Raised when a template fails to parse or has invalid variable names."""


# Jinja's own name for the filter, and its documented alias.
_DEFAULT_FILTERS = frozenset({"default", "d"})


@dataclass
class FieldSpec:
    var_name: str
    prefix: str
    rest: str
    label: str
    source_pos: int
    parent: str | None = None
    # The literal from ``{{ S_x | default("...") }}``, or None. Jinja applies
    # it at render time; this copy exists so the form can show it beforehand.
    default: str | int | float | bool | None = None
    # Whether the variable carries a default at all. Wider than `default`,
    # which is None for a non-literal like ``default(S_other)`` -- the field is
    # still optional, there is just nothing to show for it.
    has_default: bool = False


@dataclass
class RadioGroup:
    name: str
    label: str
    source_pos: int
    options: list[tuple[str, str]] = dc_field(default_factory=list)  # (value, label)
    parent: str | None = None
    default: str | None = None  # option value preselected by a truthy default


@dataclass
class ParsedTemplate:
    source: str
    fields: list[FieldSpec]
    radio_groups: list[RadioGroup]


def _label_for(rest: str) -> str:
    first, *others = rest.split("_")
    return " ".join([first.capitalize(), *others])


def _defaults_in(ast: nodes.Node) -> tuple[dict[str, str | int | float | bool], set[str]]:
    """Find the variables carrying a ``default`` filter, and its literal value.

    Returns the literals keyed by variable, and the names of *every* variable
    with a default whether or not the value could be read. The two differ for
    ``{{ S_x | default(S_other) }}``: nothing can be shown for it in a form
    built before anything is rendered, but the field still has a default, so
    it still may be left blank and Jinja still resolves it at render time.
    Guessing at the value would be worse than showing none.

    The first literal for a variable wins. Writing two is an authoring slip and
    a form can only show one, so it shows the one a reader meets first -- note
    that Jinja applies each occurrence independently, so a template with two
    different defaults for one variable renders both.
    """
    literals: dict[str, str | int | float | bool] = {}
    defaulted: set[str] = set()
    for node in ast.find_all(nodes.Filter):
        if node.name not in _DEFAULT_FILTERS or not isinstance(node.node, nodes.Name):
            continue
        defaulted.add(node.node.name)
        if node.args and isinstance(node.args[0], nodes.Const):
            literals.setdefault(node.node.name, node.args[0].value)
    return literals, defaulted


def _first_occurrence(name: str, source: str) -> int:
    match = re.search(rf"\b{re.escape(name)}\b", source)
    return match.start() if match else len(source)


def parse_template(source: str) -> ParsedTemplate:
    try:
        ast = _ENV.parse(source)
    except TemplateSyntaxError as exc:
        raise TemplateValidationError(
            f"Template syntax error: {exc.message} (line {exc.lineno})"
        ) from exc

    names = find_undeclared_variables(ast)

    invalid = sorted(n for n in names if not _NAME_RE.match(n))
    if invalid:
        raise TemplateValidationError(
            "Unrecognized variable name(s): "
            + ", ".join(invalid)
            + ". Every variable must start with S_, P_, N_, B_, or R_."
        )

    defaults, defaulted = _defaults_in(ast)

    fields: list[FieldSpec] = []
    groups: dict[str, RadioGroup] = {}
    # (position, option) per group, so the earliest truthy default wins once
    # the options have been sorted back into source order.
    radio_defaults: dict[str, list[tuple[int, str]]] = {}

    for name in names:
        match = _NAME_RE.match(name)
        prefix, rest = match.group("prefix"), match.group("rest")
        pos = _first_occurrence(name, source)

        if prefix == "R":
            radio_match = _RADIO_RE.match(rest)
            if not radio_match:
                raise TemplateValidationError(
                    f"Radio variable '{name}' must follow R_<group>_<option> "
                    "(group must be a single word, no underscores)."
                )
            group_name, option = radio_match.group("group"), radio_match.group("option")
            group = groups.setdefault(
                group_name,
                RadioGroup(name=group_name, label=_label_for(group_name), source_pos=pos),
            )
            # names comes from a set (arbitrary order), so options must carry
            # their own position and be sorted after the loop, not appended
            # in iteration order.
            group.options.append((pos, option, _label_for(option)))
            group.source_pos = min(group.source_pos, pos)
            # A radio cannot be un-selected once one option is checked, so a
            # default here can only mean "start on this option" -- never a
            # fallback for an empty submission. A falsy default says nothing
            # about which option to start on, so only truthy ones count.
            if defaults.get(name):
                radio_defaults.setdefault(group_name, []).append((pos, option))
        else:
            fields.append(
                FieldSpec(
                    var_name=name,
                    prefix=prefix,
                    rest=rest,
                    label=_label_for(rest),
                    source_pos=pos,
                    default=defaults.get(name),
                    has_default=name in defaulted,
                )
            )

    # Conditional fields: a field is a child of checkbox B_<base> when its
    # own name starts with "<base>_". Longest base wins if several match.
    checkbox_bases = [f.rest for f in fields if f.prefix == "B"]

    def find_parent(rest: str) -> str | None:
        candidates = [base for base in checkbox_bases if rest.startswith(base + "_")]
        return max(candidates, key=len) if candidates else None

    for spec in fields:
        if spec.prefix != "B":
            spec.parent = find_parent(spec.rest)
    for group in groups.values():
        # A radio group's name never contains "_" (see _RADIO_RE), so it can
        # only ever be a *direct* child of a checkbox (R_admin_* under
        # B_admin), never nested via startswith like S_/P_/N_ fields are.
        group.parent = group.name if group.name in checkbox_bases else None

    fields.sort(key=lambda f: f.source_pos)
    radio_groups = sorted(groups.values(), key=lambda g: g.source_pos)
    for group in radio_groups:
        candidates = radio_defaults.get(group.name)
        if candidates:
            group.default = min(candidates)[1]
        group.options.sort(key=lambda item: item[0])
        group.options = [(option, label) for _pos, option, label in group.options]

    return ParsedTemplate(source=source, fields=fields, radio_groups=radio_groups)
