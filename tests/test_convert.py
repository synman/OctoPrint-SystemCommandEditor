import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "sce_convert", os.path.join(HERE, "..", "octoprint_systemcommandeditor", "convert.py")
)
convert_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(convert_module)
convert = convert_module.convert
command_count = convert_module.command_count


def cmd(action, **extra):
    entry = {"action": action, "name": action.title(), "command": "echo " + action}
    entry.update(extra)
    return entry


DIV = {"action": "divider"}


def test_empty_inputs_give_empty_list():
    assert convert([], []) == ([], {"duplicates": [], "restored": [], "invalid": 0, "dividers_dropped": 0})
    assert convert(None, None)[0] == []


def test_system_list_is_kept_in_order():
    system = [cmd("a"), DIV, cmd("b")]
    actions, report = convert(system, [])
    assert actions == system
    assert report["duplicates"] == [] and report["restored"] == []


def test_duplicates_from_the_old_editor_collapse_to_the_first():
    first = cmd("restart", name="Restart")
    system = [first, cmd("restart", name="Restart copy"), cmd("restart"), cmd("other")]
    actions, report = convert(system, [])
    assert actions == [first, cmd("other")]
    assert report["duplicates"] == ["restart", "restart"]


def test_commands_only_in_the_legacy_copy_are_restored_after_a_divider():
    system = [cmd("a")]
    legacy = [cmd("a"), DIV, cmd("b")]
    actions, report = convert(system, legacy)
    assert actions == [cmd("a"), DIV, cmd("b")]
    assert report["restored"] == ["b"]


def test_wiped_system_list_is_restored_from_the_legacy_copy():
    # issue #1: the old plugin could leave system.actions empty while its own copy still held the commands
    legacy = [cmd("a"), cmd("b")]
    actions, report = convert([], legacy)
    assert actions == legacy
    assert report["restored"] == ["a", "b"]


def test_legacy_copy_matching_system_adds_nothing():
    system = [cmd("a"), DIV, cmd("b")]
    actions, report = convert(system, [cmd("a"), DIV, cmd("b")])
    assert actions == system
    assert report["restored"] == []


def test_dividers_collapse_and_ends_are_trimmed():
    actions, report = convert([DIV, DIV, cmd("a"), DIV, DIV, cmd("b"), DIV], [])
    assert actions == [cmd("a"), DIV, cmd("b")]
    assert report["dividers_dropped"] == 4


def test_named_divider_survives_at_the_top_and_wins_a_collapse():
    named = {"action": "divider", "name": "Printer"}
    actions, _ = convert([named, cmd("a"), DIV, {"action": "divider", "name": "Host"}, cmd("b")], [])
    assert actions == [named, cmd("a"), {"action": "divider", "name": "Host"}, cmd("b")]


def test_empty_optional_values_are_dropped_and_unknown_keys_kept():
    entry = cmd("a", confirm="", icon="", async_=None)
    entry.pop("async_")
    entry.update({"async": False, "fresh_credentials": False, "ignore": False, "custom": 1})
    actions, _ = convert([entry], [])
    assert actions == [{"action": "a", "name": "A", "command": "echo a", "custom": 1}]


def test_invalid_entries_are_skipped():
    actions, report = convert([cmd("a"), {"name": "no action"}, "junk", {"action": ""}], [])
    assert actions == [cmd("a")]
    assert report["invalid"] == 3


def test_conversion_is_idempotent():
    system = [DIV, cmd("a"), cmd("a"), DIV, DIV, cmd("b")]
    once, _ = convert(system, [cmd("c")])
    twice, report = convert(once, [])
    assert twice == once
    assert report == {"duplicates": [], "restored": [], "invalid": 0, "dividers_dropped": 0}


def test_never_fewer_commands_than_either_input_without_duplicates():
    system = [cmd("a"), cmd("b")]
    legacy = [cmd("c")]
    actions, _ = convert(system, legacy)
    assert command_count(actions) == 3
