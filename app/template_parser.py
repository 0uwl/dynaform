"""Extract, validate, and group DynaForm variables from a Jinja2 template.

Syntax (see dynaform.md / plan.md): every undeclared template variable must
be named ``<PREFIX>_<name>`` where PREFIX is one of S/P/N/B/R (text /
password / number / checkbox / radio). Radio variables are further named
``R_<group>_<option>`` and are grouped into one radio-button set per group.

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
from jinja2.meta import find_undeclared_variables
from jinja2.sandbox import SandboxedEnvironment

_ENV = SandboxedEnvironment()

# Provisional -- see HANDOFF.md. Bounds how far a template's reuse graph is
# walked before parsing refuses rather than following it (or looping) forever.
MAX_TEMPLATE_DEPTH = 10
MAX_TEMPLATES_REFERENCED = 50

_REF_NODE_TYPES = (nodes.Include, nodes.Import, nodes.FromImport)

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


class _RefWalker:
    """Walks a template's ``extends``/``include``/``import`` graph for the
    variables that actually reach the render, the way Jinja itself would.

    Two things Jinja's own ``find_undeclared_variables`` gets wrong for our
    purposes, both worth the extra code (see HANDOFF.md):

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

    What it deliberately leaves alone (also in HANDOFF.md, not a gap to
    close later without a reason): stray output outside a child's blocks --
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
            top = self._without_blocks(template)
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

    def _without_blocks(self, ast: nodes.Template) -> nodes.Template:
        """A copy of `ast` with every block's body emptied.

        What's left is exactly the content that isn't behind a (possibly
        dead) block -- handled instead, correctly, by
        ``_effective_block_vars``.
        """
        pruned = copy.deepcopy(ast)
        pruned.environment = _ENV  # deepcopy would otherwise clone it too
        for block in pruned.find_all(nodes.Block):
            block.body = []
        return pruned

    def _isolate_block(self, ast: nodes.Template, name: str) -> nodes.Template:
        """A copy of `ast` with every block *except* `name` emptied.

        Keeping the rest of `ast` intact (its own top-level `{% set %}` /
        `{% import %}` / `{% extends %}`) is what lets ``super()`` and a
        template's own top-level declarations resolve correctly for its own
        block -- see the class docstring.
        """
        isolated = copy.deepcopy(ast)
        isolated.environment = _ENV  # deepcopy would otherwise clone it too
        keep = next(b for b in isolated.find_all(nodes.Block) if b.name == name)
        for block in isolated.find_all(nodes.Block):
            if block is not keep:
                block.body = []
        return isolated

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
            # variables are not fillable from this form -- see HANDOFF.md
            # finding 5, "without context" is isolated from our context.
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

    names = _RefWalker(resolve).collect(ast)

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
