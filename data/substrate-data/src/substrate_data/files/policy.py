"""Who may touch which path, beyond the volume's grants.

Grants decide whether a caller may use a volume at all. A `PathPolicy` narrows that per path. The
default, `OwnerPrefixPolicy`, implements the ownership convention for delegated tokens (an agent acting
for a user): `users/{sub}/**` belongs to that subject. Tenancy work can add policies by implementing
the protocol and replacing `FilesService.policy`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from substrate_data.auth.principal import Principal
from substrate_data.core.errors import Problem

Action = Literal["read", "write"]
USERS = "users"
TENANT_ADMIN_GROUP = "tenant-admins"


class PathPolicy(Protocol):
    def authorize(self, caller: Principal, path: str, action: Action) -> None:
        """Raise `403 path-forbidden` unless `caller` may `action` the file at `path`."""

    def authorize_prefix(
        self, caller: Principal, prefix: str, action: Action
    ) -> Callable[[str], bool]:
        """Check an operation over everything under `prefix`. Returns a predicate for the entries
        (a file path, or `dir/`) the caller may see, which listings apply; raises when the whole
        operation is out of bounds."""


def forbidden() -> Problem:
    return Problem(
        403, "path-forbidden", "This path belongs to another user; the token may not use it"
    )


class AllowAll:
    def authorize(self, caller: Principal, path: str, action: Action) -> None:
        return None

    def authorize_prefix(self, caller: Principal, prefix: str, action: Action):
        return lambda _entry: True


class OwnerPrefixPolicy:
    """`users/{sub}/**` is readable and writable only by that subject, for delegated tokens.

    Ordinary user tokens (no `actor`) are limited by grants alone; so are admins and tenant admins.
    Paths outside `users/` follow the volume's grants.
    """

    def exempt(self, caller: Principal) -> bool:
        return caller.actor is None or caller.is_admin or TENANT_ADMIN_GROUP in caller.groups

    def authorize(self, caller: Principal, path: str, action: Action) -> None:
        if self.exempt(caller):
            return
        parts = path.split("/")
        if parts[0] == USERS and (len(parts) < 2 or parts[1] != caller.subject):
            raise forbidden()

    def authorize_prefix(self, caller: Principal, prefix: str, action: Action):
        if self.exempt(caller):
            return lambda _entry: True
        everyone = lambda _entry: True  # noqa: E731
        visible = lambda entry: self._visible(caller, entry)  # noqa: E731
        if prefix.startswith(USERS + "/"):
            rest = prefix[len(USERS) + 1 :]
            if "/" in rest:  # a whole subject's folder: theirs or not
                if rest.split("/", 1)[0] != caller.subject:
                    raise forbidden()
                return everyone
            spans = True  # `users/` or a partial name such as `users/al`
        else:
            spans = (USERS + "/").startswith(prefix)  # "", "u", "users", …
        if not spans:
            return everyone
        if action == "write":
            raise forbidden()
        return visible

    def _visible(self, caller: Principal, entry: str) -> bool:
        parts = entry.split("/")
        if parts[0] != USERS or len(parts) < 2 or parts[1] == "":
            return True  # outside users/, or the `users/` folder itself
        return parts[1] == caller.subject
