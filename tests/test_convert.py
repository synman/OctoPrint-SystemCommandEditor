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


free_key = convert_module.free_key
place_readouts = convert_module.place_readouts


def placement(id_):
    return {"action": id_, "name": id_.title(), "device": id_}


def test_convert_keeps_placement_entries_and_counts_only_commands():
    system = [cmd("a"), placement("disk"), DIV, cmd("b")]
    actions, report = convert(system, [])
    assert actions == system
    assert report["invalid"] == 0
    assert command_count(actions) == 2


def test_free_key_returns_base_when_unused_and_suffixes_on_collision():
    assert free_key("disk", {"memory", "divider"}) == "disk"
    assert free_key("disk", {"disk"}) == "disk_2"
    assert free_key("disk", {"disk", "disk_2"}) == "disk_3"
    assert free_key("divider", {"divider"}) == "divider_2"


def test_place_readouts_appends_placement_and_strips_section():
    devices = [{"id": "disk", "type": "readout", "name": "Disk", "section": "Status"}]
    new_devices, actions, changed = place_readouts(devices, [cmd("a")])
    assert changed is True
    assert new_devices == [{"id": "disk", "type": "readout", "name": "Disk"}]
    assert actions == [cmd("a"), {"action": "divider", "name": "Status"}, {"action": "disk", "name": "Disk", "device": "disk"}]


def test_place_readouts_avoids_key_collision_with_a_command():
    devices = [{"id": "a", "type": "readout", "name": "A", "section": ""}]
    _, actions, _ = place_readouts(devices, [cmd("a")])
    assert actions[-1] == {"action": "a_2", "name": "A", "device": "a"}


def test_place_readouts_no_readouts_is_unchanged():
    devices = [{"id": "sw", "type": "switch", "name": "Sw", "section": "X"}]
    new_devices, actions, changed = place_readouts(devices, [cmd("a")])
    assert changed is False
    assert new_devices is devices
    assert actions == [cmd("a")]


def test_a_run_entry_without_a_device_is_not_a_placement():
    entry = {"action": "boot", "name": "Boot", "run": {"plugin": "p", "helper": "boot"}}
    assert convert_module._is_placement(entry) is False


def test_an_entry_naming_its_readout_is_a_placement():
    assert convert_module._is_placement({"action": "disk", "name": "Disk", "device": "disk"}) is True


progress_format = convert_module.progress_format


def test_progress_format_gives_a_progress_control_the_percent_format_whatever_its_name():
    devices = [{"id": "x", "type": "action", "name": "My job", "read": {"printer": "progress"}},
               {"id": "c", "type": "switch", "read": {"printer": "state"}}]
    fixed, changed = progress_format(devices)
    assert changed is True
    assert fixed[0] == {"id": "x", "type": "action", "name": "My job", "read": {"printer": "progress"},
                        "format": "{value:.0f}%"}
    assert fixed[1] is devices[1]
    assert "format" not in devices[0]


def test_progress_format_keeps_a_format_already_set_and_leaves_other_configs_untouched():
    kept = [{"id": "x", "type": "readout", "read": {"printer": "progress"}, "format": "{value:.1f} pct"}]
    assert progress_format(kept) == (kept, False)
    other = [{"id": "s", "type": "readout", "status": "echo hi"}, {"id": "m", "read": {"mqtt": "a/b"}}]
    fixed, changed = progress_format(other)
    assert changed is False and fixed is other
    assert progress_format(None) == (None, False)
