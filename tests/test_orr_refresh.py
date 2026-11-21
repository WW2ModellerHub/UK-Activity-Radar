"""The official figures refresh themselves, and odd downloads are refused."""
import json

from app import seo_pages
from app.orr import refresh

PUNCT = "﻿Time Period,Operator,Recorded Station Stops,Early,On Time Within 59 Seconds,Time To 3,Time To 5,Time To 10,Time To 15,Time To 20,Time To 30,Minutes 15 Plus,Minutes 20 Plus,Minutes 30 Plus,Early Maa,On Time Within 59 Seconds Maa,Time To 3 Maa,Time To 5 Maa,Time To 10 Maa,Time To 15 Maa,Time To 20 Maa,Time To 30 Maa,Minutes 15 Plus Maa,Minutes 20 Plus Maa,Minutes 30 Plus Maa\n"
CANC = "﻿Time Period,National Or Operator,Number Of Trains Planned,Number Of Trains Part Cancelled,Number Of Trains Full Cancelled,Cancellation Number,A,B,C,D,Periodic Cancellations Percentage,Moving Annual Average Cancellations Percentage\n"


class Reply:
    def __init__(self, text):
        self.text, self.content = text, text.encode()

    def raise_for_status(self):
        return None


def _csvs(operators):
    punct, canc = PUNCT, CANC
    for op in operators:
        punct += f"Apr 2026 to Mar 2027 (Period 08),{op},1000,30,70.5,85,90,95,98,99,99.5,2,1,0.5,[z],66.1,[z],[z],[z],[z],[z],[z],[z],[z],[z]\n"
        canc += f"Apr 2026 to Mar 2027 (Period 08),{op},1000,10,20,25,1,1,1,1,2.5,3.1\n"
    return punct, canc


def test_new_punctuality_figures_are_saved_and_used_straight_away(tmp_path, monkeypatch):
    ops = ["Great Britain"] + [f"Operator {i}" for i in range(20)]
    punct, canc = _csvs(ops)

    class Session:
        def get(self, url, **_):
            return Reply(punct if "1681" in url else canc)
    monkeypatch.setattr(seo_pages, "_PERFORMANCE", None)
    assert refresh.refresh_performance(tmp_path, Session()) is True
    saved = json.loads((tmp_path / "operator_performance.json").read_text())
    assert saved["operators"]["Operator 3"][-1] == {"period": "2026-27 P8", "on_time": 70.5, "within_3": 85.0,
                                                   "within_15": 98.0, "on_time_year": 66.1, "cancelled": 2.5, "cancelled_year": 3.1}
    assert seo_pages.operator_performance("Operator 3")[1][-1]["period"] == "2026-27 P8"
    assert not refresh.due(tmp_path / "operator_performance.json", 7)


def test_an_odd_download_keeps_the_figures_in_use(tmp_path, monkeypatch):
    punct, canc = _csvs(["Great Britain", "Only One"])

    class Session:
        def get(self, url, **_):
            return Reply(punct if "1681" in url else canc)
    before = {"operators": {"kept": []}}
    monkeypatch.setattr(seo_pages, "_PERFORMANCE", before)
    assert refresh.refresh_performance(tmp_path, Session()) is False
    assert seo_pages._PERFORMANCE is before and not (tmp_path / "operator_performance.json").exists()
