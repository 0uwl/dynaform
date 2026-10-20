"""Extract, validate, and group DynaForm variables from a Jinja2 template.

Syntax: every undeclared template variable must
be named ``<PREFIX>_<NAME>`` where <PREFIX> is one of S/P/N/B/R (text /
password / number / checkbox / radio). Radio variables are further named
``R_<group>_<option>`` and are grouped into one radio-button set per group.
``L_<name>`` is a repeatable list, iterated as ``{% for x in L_name %}`` with
its row fields read as ``x.S_path``, ``x.N_port`` and so on.

A template may also reuse others via ``{% extends %}``, ``{% include %}``,
``{% import %}`` and ``{% from ... import %}``. Resolving *what those refer
to* is the caller's job (a ``resolve`` callable, so this module stays free of
Flask); working out *which variables end up on the form* is this module's --
see ``_RefWalker``.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field

from jinja2 import TemplateSyntaxError, nodes
from jinja2.exceptions import SecurityError
from jinja2.meta import find_undeclared_variables
from jinja2.sandbox import SandboxedEnvironment

MAX_POWER_BITS = 100_000


def _identity(value, *args, **kwargs):
    return value


# Per-variable metadata, written as filters: ``{{ S_host | group("Server") }}``.
# They only mark the variable for the form; at render time they hand the value
# back untouched (an undefined one stays undefined, so `default` still works).
META_FILTERS = {
    name: _identity for name in ("label", "help", "placeholder", "required", "optional", "group")
}


class BoundedSandbox(SandboxedEnvironment):
    """The sandbox, minus integer `**` results big enough to pin a worker.

    {{ 9 ** 999999999 }} is pure CPU in C and never comes back to Python, so
    nothing but gunicorn's timeout would stop it. The check is on the result's
    size rather than the exponent, since Jinja's `**` is left-associative and
    ((10 ** 1000) ** 1000) ** 1000 gets there in small steps.

    Intercepting `**` also keeps Jinja from constant-folding it at compile
    time -- which find_undeclared_variables triggers, so parsing alone would
    otherwise do the arithmetic. Memory blow-ups ("a" * 10**9,
    |center(10**9), ...) are too many to list here; the per-worker memory
    limit in gunicorn.conf.py turns those into a MemoryError instead.
    """

    intercepted_binops = frozenset({"**"})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters.update(META_FILTERS)

    def call_binop(self, context, operator, left, right):
        if (
            operator == "**"
            and isinstance(left, int)
            and isinstance(right, int)
            and left.bit_length() * right > MAX_POWER_BITS
        ):
            raise SecurityError(f"{left} ** {right} is too large to compute")
        return super().call_binop(context, operator, left, right)


_ENV = BoundedSandbox()

# Bounds how far a template's reuse graph is
# walked before parsing refuses rather than following it (or looping) forever.
MAX_TEMPLATE_DEPTH = 10
MAX_TEMPLATES_REFERENCED = 50

_REF_NODE_TYPES = (nodes.Include, nodes.Import, nodes.FromImport)

_NAME_RE = re.compile(r"^(?P<prefix>[SPNBRL])_(?P<rest>[A-Za-z][A-Za-z0-9_]*)$")
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
    # Shown while the `parent` checkbox is *un*ticked: the field is only used
    # in the {% else %} of {% if B_<parent> %}. See `_else_children`.
    show_when_unchecked: bool = False
    help: str | None = None
    placeholder: str | None = None
    # True from ``| required`` (beats a default), False from ``| optional``,
    # None to infer from parent / default.
    required: bool | None = None
    group: str | None = None  # fieldset heading, from ``| group("...")``


@dataclass
class RadioGroup:
    name: str
    label: str
    source_pos: int
    options: list[tuple[str, str]] = dc_field(default_factory=list)  # (value, label)
    parent: str | None = None
    default: str | None = None  # option value preselected by a truthy default
    group: str | None = None  # fieldset heading, from ``| group("...")`` on an option


@dataclass
class ListSpec:
    name: str  # the full variable, e.g. "L_locations"
    label: str
    source_pos: int
    fields: list[FieldSpec]  # one row's fields
    group: str | None = None


@dataclass
class ParsedTemplate:
    fields: list[FieldSpec]
    radio_groups: list[RadioGroup]
    lists: list[ListSpec]


def _label_for(rest: str) -> str:
    first, *others = rest.split("_")
    return " ".join([first.capitalize(), *others])


_TEXT_FILTERS = frozenset({"label", "help", "placeholder", "group"})
_FLAG_FILTERS = frozenset({"required", "optional"})
# These hand the value back as it came, so a filter further out still sees the
# variable itself: ``S_x | label("X") | default("a")`` defaults S_x.
_TRANSPARENT = _DEFAULT_FILTERS | META_FILTERS.keys()

# The metadata filters each kind of variable takes (`default` is always fine).
# R_ means one option of a radio group; L_ the iterable of a list loop.
_ALLOWED = {
    "S": _TEXT_FILTERS | _FLAG_FILTERS,
    "P": _TEXT_FILTERS | _FLAG_FILTERS,
    "N": _TEXT_FILTERS | _FLAG_FILTERS,
    "B": frozenset({"label", "help", "group"}),
    "R": frozenset({"label", "group"}),
    "L": frozenset({"label", "group"}),
}


def _bare_name(node: nodes.Node) -> str | None:
    return node.name if isinstance(node, nodes.Name) else None


def _filter_value(node: nodes.Filter):
    """What a metadata filter carries: its text, True for a flag, or for
    `default` the literal (None if it is not one)."""
    if node.name in _TEXT_FILTERS:
        arg = node.args[0] if len(node.args) == 1 else None
        if (
            node.kwargs or node.dyn_args or node.dyn_kwargs
            or not isinstance(arg, nodes.Const)
            or not isinstance(arg.value, str) or not arg.value.strip()
        ):
            raise TemplateValidationError(
                f'|{node.name} takes one non-empty text literal, e.g. |{node.name}("Server").'
            )
        return arg.value
    if node.name in _FLAG_FILTERS:
        if node.args or node.kwargs or node.dyn_args or node.dyn_kwargs:
            raise TemplateValidationError(f"|{node.name} takes no arguments.")
        return True
    if node.args and isinstance(node.args[0], nodes.Const):
        return node.args[0].value
    return None


def _merge_meta(into: dict[str, object], new: dict[str, object]) -> None:
    """First occurrence wins -- except that a literal `default` beats an
    earlier non-literal one, which only says the field is optional."""
    for name, value in new.items():
        if name not in into or (name == "default" and into[name] is None):
            into[name] = value


def _filters_in(
    ast: nodes.Node,
    key: Callable[[nodes.Node], str | None] = _bare_name,
) -> dict[str, dict[str, object]]:
    """Find the metadata (and `default`) filters on each variable.

    Returns {variable: {filter: value}}, see `_filter_value`. The presence of
    "default" is wider than a literal value: nothing can be shown for
    ``{{ S_x | default(S_other) }}`` before anything is rendered, but the
    field still technically has a default, so it may be left blank and Jinja
    resolves it at render time.

    Filters stacked on one variable are all found. Jinja applies each
    occurrence independently, so with a variable repeated the first one wins.

    `key` names the variable a node stands for, or None to skip it; list rows
    pass one that reads ``loc.S_x`` as ``S_x``.
    """
    found: dict[str, dict[str, object]] = {}
    for node in ast.find_all(nodes.Filter):
        if node.name not in _TRANSPARENT:
            continue
        base = node.node
        while isinstance(base, nodes.Filter) and base.name in _TRANSPARENT:
            base = base.node
        if (var := key(base)) is not None:
            name = "default" if node.name in _DEFAULT_FILTERS else node.name
            _merge_meta(found.setdefault(var, {}), {name: _filter_value(node)})
    return found


def _check_meta(var: str, meta: dict[str, object], kind: str, in_row: bool = False) -> None:
    for name in meta:
        if name == "default":
            continue
        if name not in _ALLOWED[kind] or (in_row and name == "group"):
            where = "in a list row" if in_row and name == "group" else "on this kind of variable"
            raise TemplateValidationError(f"|{name} cannot be used {where} ({var}).")
    if "required" in meta and "optional" in meta:
        raise TemplateValidationError(f"{var} cannot be both |required and |optional.")


def _apply_meta(spec: FieldSpec, meta: dict[str, object]) -> None:
    spec.has_default = "default" in meta
    spec.default = meta.get("default")
    spec.label = meta.get("label", spec.label)
    spec.help = meta.get("help")
    spec.placeholder = meta.get("placeholder")
    spec.group = meta.get("group")
    spec.required = True if "required" in meta else False if "optional" in meta else None


def _iterable_name(node: nodes.Node) -> nodes.Node:
    """The ``L_`` name under any metadata filters: ``L_x | group("Y")``."""
    while isinstance(node, nodes.Filter) and node.name in META_FILTERS:
        node = node.node
    return node


# Prefixes a list row may hold. Radio groups span several variables and lists
# nest awkwardly, so both stay out of rows until there is a reason for them.
_ROW_PREFIXES = frozenset("SPNB")


def _list_specs(
    ast: nodes.Template, source: str, list_names: set[str], meta: dict[str, dict[str, object]]
) -> list[ListSpec]:
    """Collect the row fields of every ``L_`` list from its ``for`` loops.

    Strict on purpose: the list may only appear as a loop's iterable, and the
    loop variable only as ``x.<FIELD>``. Anything else (``L_x | length``, a
    bare ``{{ x }}``, a tuple target) is refused rather than guessed at.

    ponytail: only the top template is searched, like `_defaults_in`; a list
    looped over inside an include/extends target is refused, not followed.
    """
    loops: dict[str, list[nodes.For]] = {name: [] for name in list_names}
    for loop in ast.find_all(nodes.For):
        iterable = _iterable_name(loop.iter)
        if isinstance(iterable, nodes.Name) and iterable.name in loops:
            loops[iterable.name].append(loop)

    iterables = {id(_iterable_name(loop.iter)) for found in loops.values() for loop in found}
    for name_node in ast.find_all(nodes.Name):
        if name_node.name in loops and id(name_node) not in iterables:
            raise TemplateValidationError(
                f"List '{name_node.name}' can only be used as "
                f"{{% for x in {name_node.name} %}}."
            )

    specs: list[ListSpec] = []
    for name, found in loops.items():
        if not found:
            raise TemplateValidationError(
                f"List '{name}' must be looped over in this template itself."
            )
        fields: dict[str, FieldSpec] = {}
        row_meta: dict[str, dict[str, object]] = {}
        keyed_bodies = []  # (loop body, row-field key) for _else_children
        for loop in found:
            if not isinstance(loop.target, nodes.Name):
                raise TemplateValidationError(
                    f"Loop over '{name}' must use a single loop variable."
                )
            target = loop.target.name
            body = nodes.Template(loop.body)

            attrs = [
                g for g in body.find_all(nodes.Getattr)
                if isinstance(g.node, nodes.Name) and g.node.name == target
            ]
            attr_ids = {id(g.node) for g in attrs}
            getattr_ids = {id(g) for g in attrs}
            keyed_bodies.append((body, lambda n, ids=getattr_ids: n.attr if id(n) in ids else None))
            if any(
                n.name == target and n.ctx == "load" and id(n) not in attr_ids
                for n in body.find_all(nodes.Name)
            ):
                raise TemplateValidationError(
                    f"Inside the loop over '{name}', use '{target}.<FIELD>' "
                    f"(for example '{target}.S_name'), not '{target}' alone."
                )

            for var, found_meta in _filters_in(
                body, key=lambda n: n.attr if id(n) in getattr_ids else None
            ).items():
                _merge_meta(row_meta.setdefault(var, {}), found_meta)

            for g in attrs:
                match = _NAME_RE.match(g.attr)
                if not match or match.group("prefix") not in _ROW_PREFIXES:
                    raise TemplateValidationError(
                        f"List field '{target}.{g.attr}' must start with "
                        "S_, P_, N_, or B_."
                    )
                fields.setdefault(g.attr, FieldSpec(
                    var_name=g.attr,
                    prefix=match.group("prefix"),
                    rest=match.group("rest"),
                    label=_label_for(match.group("rest")),
                    source_pos=_first_occurrence(f"{target}.{g.attr}", source),
                ))

        if not fields:
            raise TemplateValidationError(f"List '{name}' has no fields to fill in.")
        for var, spec in fields.items():
            _check_meta(f"{target}.{var}", row_meta.get(var, {}), spec.prefix, in_row=True)
            _apply_meta(spec, row_meta.get(var, {}))
        _assign_parents(list(fields.values()), _else_children(keyed_bodies))
        rest = _NAME_RE.match(name).group("rest")
        list_meta = meta.get(name, {})
        _check_meta(name, list_meta, "L")
        specs.append(ListSpec(
            name=name,
            label=list_meta.get("label", _label_for(rest)),
            source_pos=_first_occurrence(name, source),
            fields=sorted(fields.values(), key=lambda f: f.source_pos),
            group=list_meta.get("group"),
        ))
    return sorted(specs, key=lambda l: l.source_pos)


def _else_children(
    keyed_trees: list[tuple[nodes.Node, Callable[[nodes.Node], str | None]]],
) -> dict[str, str]:
    """Variables used only in the {% else %} of a plain {% if B_<base> %}, each
    mapped to its <base>.

    Plain means the test is the checkbox itself and there is no {% elif %}, so
    the else branch runs exactly when the box is unticked. A variable used
    anywhere outside that branch as well stays unconditional. Each tree comes
    with a `key` naming the variable a node stands for (as in `_defaults_in`).

    ponytail: only the trees passed in are searched; a variable also used in
    an included template still counts as else-only.
    """
    branch_of: dict[int, str] = {}  # node id -> base of the innermost else holding it
    for tree, key in keyed_trees:
        for node in tree.find_all(nodes.If):
            test = key(node.test)
            if node.elif_ or not test or not test.startswith("B_"):
                continue
            for n in nodes.Template(node.else_).find_all(nodes.Node):
                branch_of[id(n)] = test[2:]

    bases: dict[str, set[str | None]] = {}
    for tree, key in keyed_trees:
        for n in tree.find_all(nodes.Node):
            if var := key(n):
                bases.setdefault(var, set()).add(branch_of.get(id(n)))
    return {var: b.pop() for var, b in bases.items() if len(b) == 1 and None not in b}


def _assign_parents(fields: list[FieldSpec], else_bases: dict[str, str]) -> list[str]:
    """Conditional fields: a field is a child of checkbox B_<base> when its
    own name starts with "<base>_". Longest base wins if several match. A
    field used only in the checkbox's {% else %} is a child too, shown while
    the box is unticked instead (`else_bases`, from `_else_children`), unless
    its name already ties it to a checkbox.

    Run once on the top-level fields and once per list row, so a row field
    only ever hangs off a checkbox in its own row. Returns the bases found.
    """
    bases = [f.rest for f in fields if f.prefix == "B"]
    for spec in fields:
        candidates = [] if spec.prefix == "B" else [
            base for base in bases if spec.rest.startswith(base + "_")
        ]
        if candidates:
            spec.parent = max(candidates, key=len)
        elif spec.var_name in else_bases:
            spec.parent, spec.show_when_unchecked = else_bases[spec.var_name], True
        else:
            spec.parent = None
    return bases


def _first_occurrence(name: str, source: str) -> int:
    match = re.search(rf"\b{re.escape(name)}\b", source)
    return match.start() if match else len(source)


class _RefWalker:
    """Walks a template's ``extends``/``include``/``import`` graph for the
    variables that actually reach the render, the way Jinja itself would.

    Two things Jinja's own ``find_undeclared_variables`` gets wrong for our
    purposes, both worth the extra code:

    * A ``{% block %}`` compiles as its own frame, so a name a template
      declares for itself at the top level (``{% set %}``, ``{% import %}``,
      a macro) reads as "undeclared" from inside that *same* template's own
      block -- even though it plainly isn't. ``_declared_names`` and the
      ``super`` exclusion in ``_effective_block_vars`` correct for that.
    * A base block a child overrides *without* calling ``super()`` never
      renders, and a child block absent from the base chain never renders
      either -- both would otherwise turn into bogus required fields.
      ``_effective_block_vars`` resolves each block name to whichever
      definition actually wins, chasing ``super()`` up the chain as needed.

    What it deliberately leaves alone (not a gap to close later without a
    reason): stray output outside a child's blocks --
    over-collecting a field beats silently dropping one that does render, and
    a wrong rule here risks the latter; and a child ``{% set %}`` shadowing a
    name the base reads, which over-collects the same field instead of
    dropping it -- correct behaviour either way, just a field asked for that
    the template always supplies itself.
    """

    def __init__(self, resolve: Callable[[str], str | None] | None):
        self._resolve = resolve or (lambda _name: None)
        self._cache: dict[str, nodes.Template] = {}

    def collect(self, ast: nodes.Template, path: tuple[str, ...] = (), depth: int = 0) -> set[str]:
        chain = self._extends_chain(ast, path, depth)
        root_block_names = {b.name for b in chain[-1].find_all(nodes.Block)}

        names: set[str] = set()
        for level, template in enumerate(chain):
            top = self._isolate_block(template, None)
            names |= find_undeclared_variables(top)
            names |= self._collect_refs(top, path, depth + level)

        for block_name in root_block_names:
            names |= self._effective_block_vars(chain, block_name, 0, path, depth)

        return names

    def _parse_named(self, name: str, path: tuple[str, ...], depth: int) -> nodes.Template:
        if name in path:
            loop = " -> ".join((*path, name))
            raise TemplateValidationError(f"Template reference cycle: {loop}")
        if depth > MAX_TEMPLATE_DEPTH:
            raise TemplateValidationError(
                f"Templates are nested more than {MAX_TEMPLATE_DEPTH} levels deep."
            )
        if name in self._cache:
            return self._cache[name]
        if len(self._cache) >= MAX_TEMPLATES_REFERENCED:
            raise TemplateValidationError(
                f"More than {MAX_TEMPLATES_REFERENCED} templates are referenced."
            )
        source = self._resolve(name)
        if source is None:
            raise TemplateValidationError(f"Referenced template not found: {name}")
        try:
            ast = _ENV.parse(source)
        except TemplateSyntaxError as exc:
            raise TemplateValidationError(
                f"Template syntax error in '{name}': {exc.message} (line {exc.lineno})"
            ) from exc
        self._cache[name] = ast
        return ast

    def _ref_name(self, template_expr: nodes.Expr, kind: str) -> str:
        if not isinstance(template_expr, nodes.Const) or not isinstance(template_expr.value, str):
            raise TemplateValidationError(
                f"Cannot resolve a dynamic {kind} target; use a literal template name."
            )
        return template_expr.value

    def _extends_chain(
        self, ast: nodes.Template, path: tuple[str, ...], depth: int
    ) -> list[nodes.Template]:
        chain = [ast]
        current, current_path, current_depth = ast, path, depth
        while True:
            extends = list(current.find_all(nodes.Extends))
            if not extends:
                return chain
            target = self._ref_name(extends[0].template, "extends")
            current = self._parse_named(target, current_path, current_depth + 1)
            current_path = (*current_path, target)
            current_depth += 1
            chain.append(current)

    def _isolate_block(self, ast: nodes.Template, name: str | None) -> nodes.Template:
        """A copy of `ast` with every block emptied except `name` and its
        own ancestor blocks (a block nested inside another one). With `name`
        None every block is emptied, leaving only the top-level content.

        Keeping the rest of `ast` intact (its own top-level `{% set %}` /
        `{% import %}` / `{% extends %}`) is what lets ``super()`` and a
        template's own top-level declarations resolve correctly for its own
        block -- see the class docstring. Ancestors have to stay too: pruning
        an enclosing block's body the way a sibling's is pruned would cut the
        target's own path back to the tree root, losing it entirely.
        """
        isolated = copy.deepcopy(ast)
        isolated.environment = _ENV  # deepcopy would otherwise clone it too
        keep = {id(b) for b in self._block_and_ancestors(isolated, name)}
        for block in isolated.find_all(nodes.Block):
            if id(block) not in keep:
                block.body = []
        return isolated

    def _block_and_ancestors(self, ast: nodes.Template, name: str) -> list[nodes.Block]:
        """The first block named `name`, plus every block that encloses it."""
        path: list[nodes.Block] = []

        def visit(node: nodes.Node) -> list[nodes.Block] | None:
            entered = isinstance(node, nodes.Block)
            if entered:
                path.append(node)
                if node.name == name:
                    return list(path)
            for child in node.iter_child_nodes():
                found = visit(child)
                if found is not None:
                    return found
            if entered:
                path.pop()
            return None

        found = visit(ast)
        return found or []

    def _declared_names(self, ast: nodes.Template) -> set[str]:
        names = {n.name for n in ast.find_all(nodes.Name) if n.ctx in ("store", "param")}
        names |= {n.target for n in ast.find_all(nodes.Import)}
        for from_import in ast.find_all(nodes.FromImport):
            names |= {n if isinstance(n, str) else n[1] for n in from_import.names}
        names |= {n.name for n in ast.find_all(nodes.Macro)}
        return names

    def _calls_super(self, block: nodes.Block) -> bool:
        return any(
            isinstance(call.node, nodes.Name) and call.node.name == "super"
            for call in block.find_all(nodes.Call)
        )

    def _collect_refs(
        self, tree: nodes.Template, path: tuple[str, ...], depth: int
    ) -> set[str]:
        names: set[str] = set()
        for ref in tree.find_all(_REF_NODE_TYPES):
            kind = "include" if isinstance(ref, nodes.Include) else "import"
            target = self._ref_name(ref.template, kind)
            target_ast = self._parse_named(target, path, depth + 1)
            if ref.with_context:
                names |= self.collect(target_ast, (*path, target), depth + 1)
            # else: resolved (so a missing one is still refused) but its
            # variables are not fillable from this form: "without context"
            # is isolated from our context.
        return names

    def _effective_block_vars(
        self,
        chain: list[nodes.Template],
        name: str,
        start: int,
        path: tuple[str, ...],
        depth: int,
    ) -> set[str]:
        """Variables for whichever definition of block `name` actually wins.

        Most-derived definition found in `chain` (search starts at `start`,
        the child end) wins outright, unless it calls ``super()``, in which
        case the next definition up the chain contributes too -- and may
        itself call ``super()``, and so on.
        """
        for level in range(start, len(chain)):
            template = chain[level]
            block = next((b for b in template.find_all(nodes.Block) if b.name == name), None)
            if block is None:
                continue
            isolated = self._isolate_block(template, name)
            names = find_undeclared_variables(isolated) - {"super"} - self._declared_names(isolated)
            names |= self._collect_refs(isolated, path, depth + level)
            if self._calls_super(block):
                names |= self._effective_block_vars(chain, name, level + 1, path, depth)
            return names
        return set()


def parse_template(
    source: str, resolve: Callable[[str], str | None] | None = None
) -> ParsedTemplate:
    try:
        ast = _ENV.parse(source)
    except TemplateSyntaxError as exc:
        raise TemplateValidationError(
            f"Template syntax error: {exc.message} (line {exc.lineno})"
        ) from exc

    try:
        names = _RefWalker(resolve).collect(ast)
    except TemplateSyntaxError as exc:  # e.g. a filter that does not exist
        raise TemplateValidationError(
            f"Template error: {exc.message} (line {exc.lineno})"
        ) from exc

    invalid = sorted(n for n in names if not _NAME_RE.match(n))
    if invalid:
        raise TemplateValidationError(
            "Unrecognized variable name(s): "
            + ", ".join(invalid)
            + ". Every variable must start with S_, P_, N_, B_, R_, or L_."
        )

    list_names = {n for n in names if n.startswith("L_")}
    meta = _filters_in(ast)
    lists = _list_specs(ast, source, list_names, meta)
    names -= list_names

    fields: list[FieldSpec] = []
    groups: dict[str, RadioGroup] = {}
    # (position, option) per group, so the earliest truthy default wins once
    # the options have been sorted back into source order.
    radio_defaults: dict[str, list[tuple[int, str]]] = {}
    radio_sections: dict[str, list[tuple[int, str]]] = {}

    for name in names:
        match = _NAME_RE.match(name)
        prefix, rest = match.group("prefix"), match.group("rest")
        pos = _first_occurrence(name, source)
        var_meta = meta.get(name, {})
        _check_meta(name, var_meta, prefix)

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
            group.options.append((pos, option, var_meta.get("label", _label_for(option))))
            if "group" in var_meta:
                radio_sections.setdefault(group_name, []).append((pos, var_meta["group"]))
            group.source_pos = min(group.source_pos, pos)
            # A radio cannot be un-selected once one option is checked, so a
            # default here can only mean "start on this option" -- never a
            # fallback for an empty submission. A falsy default says nothing
            # about which option to start on, so only truthy ones count.
            if var_meta.get("default"):
                radio_defaults.setdefault(group_name, []).append((pos, option))
        else:
            spec = FieldSpec(
                var_name=name,
                prefix=prefix,
                rest=rest,
                label=_label_for(rest),
                source_pos=pos,
            )
            _apply_meta(spec, var_meta)
            fields.append(spec)

    checkbox_bases = _assign_parents(fields, _else_children([(
        ast, lambda n: n.name if isinstance(n, nodes.Name) and n.ctx == "load" else None
    )]))
    for group in groups.values():
        # A radio group's name never contains "_" (see _RADIO_RE), so it can
        # only ever be a *direct* child of a checkbox (R_admin_* under
        # B_admin), never nested via startswith like S_/P_/N_ fields are.
        group.parent = group.name if group.name in checkbox_bases else None
        if group.name in radio_sections:
            group.group = min(radio_sections[group.name])[1]

    # A conditional field has no business leaving its checkbox's section.
    sections = {f.rest: f.group for f in fields if f.prefix == "B"}
    for spec in [*fields, *groups.values()]:
        if spec.parent and spec.group is None:
            spec.group = sections.get(spec.parent)

    fields.sort(key=lambda f: f.source_pos)
    radio_groups = sorted(groups.values(), key=lambda g: g.source_pos)
    for group in radio_groups:
        candidates = radio_defaults.get(group.name)
        if candidates:
            group.default = min(candidates)[1]
        group.options.sort(key=lambda item: item[0])
        group.options = [(option, label) for _pos, option, label in group.options]

    return ParsedTemplate(fields=fields, radio_groups=radio_groups, lists=lists)
