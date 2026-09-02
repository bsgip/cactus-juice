import pytest
from assertical.fake.generator import enumerate_class_properties, generate_class_instance

from cactus_juice.csipaus.dto import ActiveValues, ControlValues, DefaultValues, HasControlValues, HasDefaultValues


@pytest.mark.parametrize("optional_is_none", [True, False])
def test_HasControlValues_protocol(optional_is_none: bool):
    """Ensures ControlValues matches the HasControlValues protocol"""

    instance = generate_class_instance(ControlValues, optional_is_none=optional_is_none)
    assert isinstance(instance, ControlValues)
    assert isinstance(instance, HasControlValues)
    assert not isinstance(instance, HasDefaultValues), "ControlValues does NOT implement the HasDefaultValues protocol"


@pytest.mark.parametrize("optional_is_none", [True, False])
def test_HasDefaultValues_protocol(optional_is_none: bool):
    """Ensures DefaultValues matches the HasDefaultValues protocol"""

    instance = generate_class_instance(DefaultValues, optional_is_none=optional_is_none)
    assert isinstance(instance, DefaultValues)
    assert isinstance(instance, HasDefaultValues)
    assert not isinstance(instance, HasControlValues), "DefaultValues does NOT implement the HasControlValues protocol"


def test_common_ControlValues_DefaultValues():
    control_values = {cp.name: cp for cp in enumerate_class_properties(ControlValues)}
    default_values = {cp.name: cp for cp in enumerate_class_properties(DefaultValues)}

    common_keys = control_values.keys() & default_values.keys()
    assert len(common_keys) > 1, "There should be a couple of common values"
    for k in common_keys:
        assert control_values[k] == default_values[k]


def test_ActiveValues_combined():
    active_values = {cp.name: cp for cp in enumerate_class_properties(ActiveValues)}
    control_values = {cp.name: cp for cp in enumerate_class_properties(ControlValues)}
    default_values = {cp.name: cp for cp in enumerate_class_properties(DefaultValues)}

    assert active_values.keys() == control_values.keys() | default_values.keys(), "ActiveValues should have everything"

    for k in default_values.keys():
        assert active_values[k] == default_values[k]
    for k in control_values.keys():
        assert active_values[k] == control_values[k]
