"""Pure lifecycle policy: commands x from-status x actor kind x edit lock, owner release."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from clarinet.exceptions.domain import (
    AuthorizationError,
    RecordEditLockedError,
    TransitionNotAllowedError,
)
from clarinet.models.actor import HumanActor, SystemActor
from clarinet.models.base import RecordStatus as S
from clarinet.services.record_lifecycle import (
    BLOCKED_TEXT,
    FINISHED_TEXT,
    NOT_FINISHED_TEXT,
    PREPARING_TEXT,
    Assign,
    Claim,
    Create,
    Edit,
    Fail,
    RecordSnapshot,
    Restart,
    SetStatus,
    Submit,
    TypeRules,
    Unassign,
    Unblock,
    allowed_commands,
    decide,
    decide_create,
    needs_inputs,
)

ROLE = "doctor"
ME, OTHER, SERVICE, TARGET = uuid4(), uuid4(), uuid4(), uuid4()


def _person(*roles: str, superuser: bool = False) -> HumanActor:
    return HumanActor(user_id=ME, is_superuser=superuser, role_names=frozenset(roles))


SYSTEM = SystemActor(service_user_id=SERVICE)
ACTORS = {
    "system": SYSTEM,
    "admin": _person("admin", ROLE),
    "owner": _person(ROLE),
    "free": _person(ROLE),
    "colleague": _person(ROLE),
    "outsider": _person("other"),
}
RECORD_OWNER = {
    "system": OTHER,
    "admin": OTHER,
    "owner": ME,
    "free": None,
    "colleague": OTHER,
    "outsider": None,
}
PERSONS = {"owner", "free", "colleague"}

COMMANDS = {
    "claim": Claim(),
    "assign": Assign(user_id=TARGET),
    "unassign": Unassign(),
    "submit": Submit(target=S.finished),
    "edit": Edit(),
    "fail": Fail(reason="r"),
    "restart": Restart(),
    "set_pause": SetStatus(target=S.pause),
    "set_finished": SetStatus(target=S.finished),
    "unblock": Unblock(),
}
ADMIN_ONLY = {"assign", "unassign", "set_pause", "set_finished"}
LOCKABLE = {"edit", "restart"}
UNCHANGED = "unchanged"


def _each(value, **but):
    table = dict.fromkeys(S, value)
    table.update({S(k): v for k, v in but.items()})
    return table


CONTRACT = {
    "claim": _each(409, pending=S.inwork, inwork=S.inwork),
    "assign": {**{s: s for s in S}, S.pending: S.inwork},
    "unassign": {**{s: s for s in S}, S.inwork: S.pending},
    "submit": _each(S.finished, blocked=409, preparing=409, finished=409),
    "edit": _each(409, finished=S.finished),
    "fail": _each(409, pending=S.failed, inwork=S.failed),
    "restart": _each(S.pending, preparing=S.preparing),
    "set_pause": _each(S.pause, pause=UNCHANGED),
    "set_finished": _each(S.finished, finished=UNCHANGED, preparing=409),
    "unblock": _each(UNCHANGED, blocked=S.pending),
}


def _claimant(kind: str):
    return SERVICE if kind == "system" else ME


def _expected(cmd_key: str, status: S, kind: str, locked: bool):
    if kind == "outsider":
        return 403
    if kind in PERSONS:
        if cmd_key in ADMIN_ONLY or kind == "colleague":
            return 403
        if cmd_key in LOCKABLE and locked and status == S.finished:
            return 409
    if cmd_key == "claim" and RECORD_OWNER[kind] not in (None, _claimant(kind)):
        return 403
    return CONTRACT[cmd_key][status]


def _rules(**kw) -> TypeRules:
    base = {
        "name": "report-rt",
        "role_name": ROLE,
        "editable": True,
        "edit_window_days": None,
        "shared_editing": False,
        "releasable": False,
    }
    base.update(kw)
    return TypeRules(**base)


def _snap(status: S, owner=None, finished_at=None, **rules) -> RecordSnapshot:
    return RecordSnapshot(
        record_id=7, status=status, user_id=owner, finished_at=finished_at, rules=_rules(**rules)
    )


CASES = [(c, s, k, locked) for c in COMMANDS for s in S for k in ACTORS for locked in (False, True)]


@pytest.mark.parametrize(("cmd_key", "status", "kind", "locked"), CASES)
def test_policy_matrix(cmd_key, status, kind, locked):
    snap = _snap(status, owner=RECORD_OWNER[kind], editable=not locked)
    expected = _expected(cmd_key, status, kind, locked)

    def run():
        return decide(COMMANDS[cmd_key], snap, ACTORS[kind], inputs="valid")

    if expected == 403:
        with pytest.raises(AuthorizationError):
            run()
    elif expected == 409:
        with pytest.raises((TransitionNotAllowedError, RecordEditLockedError)):
            run()
    elif expected == UNCHANGED:
        effect = run()
        assert (effect.to_status, effect.owner) == (status, snap.user_id)
    else:
        assert run().to_status == expected


@pytest.mark.parametrize(
    ("status", "text"),
    [(S.blocked, BLOCKED_TEXT), (S.preparing, PREPARING_TEXT), (S.finished, FINISHED_TEXT)],
)
@pytest.mark.parametrize("actor", [SYSTEM, _person(ROLE)])
def test_submit_refusals_keep_their_texts(status, text, actor):
    with pytest.raises(TransitionNotAllowedError) as exc:
        decide(Submit(target=S.finished), _snap(status), actor)
    assert str(exc.value) == text


def test_other_verbatim_texts():
    with pytest.raises(TransitionNotAllowedError, match=f"^{NOT_FINISHED_TEXT}$"):
        decide(Edit(), _snap(S.pending), SYSTEM)
    with pytest.raises(TransitionNotAllowedError) as exc:
        decide(Fail(reason="x"), _snap(S.finished), SYSTEM)
    assert str(exc.value) == "Cannot fail record in 'finished' status. Allowed: pending, inwork."
    with pytest.raises(TransitionNotAllowedError) as exc:
        decide(Submit(target=S.pending), _snap(S.inwork), SYSTEM)
    assert str(exc.value) == "Invalid submit status 'pending'. Allowed: finished, failed."
    with pytest.raises(TransitionNotAllowedError) as exc:
        decide(SetStatus(target=S.finished), _snap(S.preparing), SYSTEM)
    assert str(exc.value) == (
        "Record 7 is still preparing — it must leave via 'pending' "
        "(with file re-validation) before 'finished'."
    )


def test_refusals_carry_code_and_status():
    with pytest.raises(TransitionNotAllowedError) as exc:
        decide(Submit(target=S.finished), _snap(S.finished), SYSTEM)
    assert (exc.value.error_code, exc.value.metadata()) == (
        "TRANSITION_NOT_ALLOWED",
        {"status": "finished"},
    )
    with pytest.raises(RecordEditLockedError) as locked:
        decide(Edit(), _snap(S.finished, owner=ME, editable=False), _person(ROLE))
    assert (locked.value.error_code, locked.value.metadata()) == (
        "RECORD_EDIT_LOCKED",
        {"status": "finished"},
    )
    with pytest.raises(TransitionNotAllowedError) as create:
        decide_create(Create(status=S.finished, owner_id=None), _rules(), SYSTEM)
    assert create.value.metadata() == {}  # no record exists yet


def test_lock_texts_and_window():
    owner = _person(ROLE)
    with pytest.raises(RecordEditLockedError) as exc:
        decide(Edit(), _snap(S.finished, owner=ME, editable=False), owner)
    assert str(exc.value) == (
        "Record 7: record type 'report-rt' does not allow changing submitted records."
    )
    old = datetime.now(UTC) - timedelta(days=10)
    with pytest.raises(RecordEditLockedError) as exc:
        decide(Restart(), _snap(S.finished, owner=ME, finished_at=old, edit_window_days=3), owner)
    assert str(exc.value) == "Record 7: editing window of 3 days after submission has passed."
    recent = datetime.now(UTC) - timedelta(days=1)
    assert decide(Edit(), _snap(S.finished, ME, recent, edit_window_days=3), owner).to_status == (
        S.finished
    )
    # A legacy row without finished_at fails open.
    assert decide(Edit(), _snap(S.finished, ME, None, edit_window_days=3), owner).to_status == (
        S.finished
    )


def test_admin_role_without_type_role_may_not_mutate():
    admin_only = _person("admin")
    for cmd in COMMANDS.values():
        with pytest.raises(AuthorizationError):
            decide(cmd, _snap(S.pending), admin_only)


def test_superuser_is_admin_and_sees_roleless_types():
    root = _person(superuser=True)
    assert decide(SetStatus(target=S.pause), _snap(S.pending), root).to_status == S.pause
    assert decide(SetStatus(target=S.pause), _snap(S.pending, role_name=None), root).to_status == (
        S.pause
    )
    with pytest.raises(AuthorizationError):
        decide(SetStatus(target=S.pause), _snap(S.pending, role_name=None), _person("admin"))


def test_shared_editing():
    colleague = _person(ROLE)
    shared = {"shared_editing": True}
    submit = decide(Submit(target=S.finished), _snap(S.inwork, OTHER, **shared), colleague)
    assert submit.owner == ME
    edit = decide(Edit(), _snap(S.finished, OTHER, **shared), colleague)
    assert (edit.owner, edit.fire) == (ME, "data_update")
    with pytest.raises(AuthorizationError):
        decide(Claim(), _snap(S.pending, OTHER, **shared), colleague)
    assert decide(Edit(), _snap(S.finished, OTHER, **shared), SYSTEM).owner == OTHER


def test_owner_effects():
    person = _person(ROLE)
    assert decide(Claim(), _snap(S.pending), person).owner == ME
    assert decide(Claim(), _snap(S.pending), SYSTEM).owner == SERVICE
    reclaim = decide(Claim(), _snap(S.inwork, ME), person)
    assert (reclaim.to_status, reclaim.owner) == (S.inwork, ME)
    assert decide(Submit(target=S.finished), _snap(S.pending), person).owner == ME
    assert decide(Submit(target=S.finished), _snap(S.pending), SYSTEM).owner == SERVICE
    failed = decide(Submit(target=S.failed), _snap(S.inwork, ME), person)
    assert (failed.to_status, failed.owner) == (S.failed, ME)
    assert decide(Unassign(), _snap(S.finished, OTHER), SYSTEM).owner is None
    assert decide(Assign(user_id=TARGET), _snap(S.finished, OTHER), SYSTEM).owner == TARGET
    assert decide(Restart(), _snap(S.finished, OTHER), SYSTEM).owner == OTHER


def test_fire_kinds():
    restart = decide(Restart(), _snap(S.pending), SYSTEM)
    assert (restart.to_status, restart.fire) == (S.pending, "invalidation")
    assert decide(Restart(), _snap(S.preparing), SYSTEM).to_status == S.preparing
    assert decide(Edit(), _snap(S.finished), SYSTEM).fire == "data_update"
    assert decide(Fail(reason="x"), _snap(S.inwork), SYSTEM).fire == "status"


@pytest.mark.parametrize("status", list(S))
@pytest.mark.parametrize("releasable", [False, True])
def test_owner_release(status, releasable):
    owner = _person(ROLE)
    snap = _snap(status, owner=ME, releasable=releasable)
    if not releasable:
        with pytest.raises(AuthorizationError):
            decide(Unassign(), snap, owner)
    elif status in (S.pending, S.inwork):
        effect = decide(Unassign(), snap, owner)
        assert (effect.to_status, effect.owner) == (S.pending, None)
    else:
        with pytest.raises(TransitionNotAllowedError, match="Cannot release a record"):
            decide(Unassign(), snap, owner)


def test_release_is_the_owners_only():
    colleague = _person(ROLE)
    with pytest.raises(AuthorizationError):
        decide(Unassign(), _snap(S.inwork, owner=OTHER, releasable=True), colleague)
    with pytest.raises(AuthorizationError):
        decide(Unassign(), _snap(S.pending, owner=None, releasable=True), colleague)


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [("invalid", S.blocked), ("valid", S.pending), ("undeclared", S.pending), (None, S.pending)],
)
def test_preparing_exit_revalidates_inputs(inputs, expected):
    effect = decide(SetStatus(target=S.pending), _snap(S.preparing), SYSTEM, inputs=inputs)
    assert effect.to_status == expected


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [("valid", S.pending), ("invalid", S.blocked), ("undeclared", S.blocked), (None, S.pending)],
)
def test_unblock_needs_valid_inputs(inputs, expected):
    assert decide(Unblock(), _snap(S.blocked), SYSTEM, inputs=inputs).to_status == expected


def test_needs_inputs():
    assert needs_inputs(SetStatus(target=S.pending), _snap(S.preparing))
    assert not needs_inputs(SetStatus(target=S.pending), _snap(S.pause))
    assert not needs_inputs(SetStatus(target=S.pause), _snap(S.preparing))
    assert needs_inputs(Unblock(), _snap(S.blocked))
    assert not needs_inputs(Unblock(), _snap(S.pending))
    assert not needs_inputs(Submit(target=S.finished), _snap(S.pending))


def test_allowed_commands_follow_the_policy():
    owner, admin = _person(ROLE), _person("admin", ROLE)
    locked = _snap(S.finished, owner=ME, editable=False)
    assert not {"edit", "restart"} & set(allowed_commands(locked, owner))
    assert {"edit", "restart", "assign", "unassign", "set_status"} <= set(
        allowed_commands(locked, admin)
    )
    assert "unassign" not in allowed_commands(_snap(S.inwork, owner=ME), owner)
    assert "unassign" in allowed_commands(_snap(S.inwork, owner=ME, releasable=True), owner)
    assert allowed_commands(_snap(S.pending), _person("other")) == []
    assert set(allowed_commands(_snap(S.pending), owner)) == {
        "claim",
        "submit",
        "fail",
        "restart",
        "unblock",
    }


RULES = _rules()


@pytest.mark.parametrize("status", list(S))
def test_create_statuses(status):
    for actor, allowed in (
        (SYSTEM, {S.pending, S.preparing}),
        (_person("admin"), {S.pending, S.preparing}),
        (_person(ROLE), {S.pending}),
    ):
        if status in allowed:
            decide_create(Create(status=status, owner_id=None), RULES, actor)
        else:
            with pytest.raises(TransitionNotAllowedError, match="A new record must start as"):
                decide_create(Create(status=status, owner_id=None), RULES, actor)


def test_create_owner_and_role():
    decide_create(Create(status=S.pending, owner_id=ME), RULES, _person(ROLE))
    with pytest.raises(AuthorizationError):
        decide_create(Create(status=S.pending, owner_id=OTHER), RULES, _person(ROLE))
    decide_create(Create(status=S.pending, owner_id=OTHER), RULES, _person("admin"))
    decide_create(Create(status=S.pending, owner_id=OTHER), RULES, SYSTEM)
    with pytest.raises(AuthorizationError):
        decide_create(Create(status=S.pending, owner_id=None), RULES, _person("other"))
    with pytest.raises(AuthorizationError):
        decide_create(
            Create(status=S.pending, owner_id=None), _rules(role_name=None), _person(ROLE)
        )
