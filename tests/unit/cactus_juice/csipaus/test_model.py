import pytest
from assertical.fake.generator import generate_class_instance

from cactus_juice.csipaus.dto import HasControlValues, HasDefaultValues
from cactus_juice.model import CSIPAusControl, CSIPAusDefault


@pytest.mark.parametrize("optional_is_none", [True, False])
def test_csipaus_protocols(optional_is_none: bool):
    c = generate_class_instance(CSIPAusControl, optional_is_none=optional_is_none)
    d = generate_class_instance(CSIPAusDefault, optional_is_none=optional_is_none, active_range=None)
    assert isinstance(c, HasControlValues)
    assert not isinstance(d, HasControlValues)
    assert not isinstance(c, HasDefaultValues)
    assert isinstance(d, HasDefaultValues)
