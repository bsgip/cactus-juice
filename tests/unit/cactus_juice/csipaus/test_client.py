from cactus_juice.csipaus.client import POW10_BY_READING_TYPE, SUPPORTED_READING_TYPES


def test_SUPPORTED_READING_TYPES_in_POW10_BY_READING_TYPE():
    """Every SUPPORTED_READING_TYPES must have a pow10"""
    for rt in SUPPORTED_READING_TYPES:
        assert rt in POW10_BY_READING_TYPE
