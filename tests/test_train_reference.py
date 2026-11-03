from pathlib import Path

from app.train_reference import UKTrainReference


BUNDLED = Path(__file__).parents[1] / "app" / "vendor" / "uk_train_dataset" / "trains.csv"


def test_exact_train_number_match_and_no_fuzzy_guess():
    reference = UKTrainReference(BUNDLED)

    matched = reference.lookup("DR 72211")
    assert matched["matched"] is True
    assert matched["number"] == "DR72211"
    assert matched["train_class"] == "OTMs"
    assert matched["match_basis"] == "exact_vehicle_number"

    assert reference.lookup("DR7221")["matched"] is False
    assert reference.lookup("1Z80")["matched"] is False
    assert reference.lookup(None)["matched"] is False


def test_bundled_train_dataset_is_available():
    reference = UKTrainReference(BUNDLED)
    assert reference.record_count > 20_000
