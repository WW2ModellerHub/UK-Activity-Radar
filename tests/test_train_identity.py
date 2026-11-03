from app.train_identity import infer_train_family, operator_name, power_type_name, trust_operator_name


def test_operator_code_is_turned_into_passenger_facing_name():
    assert operator_name("XC") == "CrossCountry"
    assert operator_name("sw") == "South Western Railway"
    assert operator_name("ZZ") == "Rail operator"
    assert operator_name("ZZ", "Known freight operator") == "Known freight operator"


def test_schedule_power_type_is_human_readable():
    assert power_type_name("DMU") == "Diesel multiple unit"


def test_numeric_trust_sector_is_turned_into_passenger_facing_name():
    assert trust_operator_name("29") == "West Midlands Trains"
    assert trust_operator_name(29) == "West Midlands Trains"
    assert trust_operator_name("999", "Rail operator") == "Rail operator"


def test_crosscountry_family_inference_is_labelled_and_not_exact():
    result = infer_train_family(
        operator_code="XC", power_type="DMU",
        origin="Edinburgh", destination="Darlington",
    )
    assert result["label"] == "Class 220/221 Voyager family"
    assert result["confidence"] == "likely"
    assert "exact unit is not" in result["explanation"]


def test_unknown_operator_does_not_invent_a_train_family():
    assert infer_train_family(operator_code="ZZ", power_type="DMU") is None
