"""Reading an invoice total that is printed only in words.

Bharat Serums prints no machine-readable grand total anywhere on the bill - just
"Total Amount in Words : FOUR LAKH THIRTEEN THOUSAND THREE HUNDRED SIXTY SIX",
and its PDF draws the glyphs with no spaces between them, so the text extracts
as one run-together blob. Without this the invoice can never be reconciled.
"""
from app.services.ocr.amount_words import _segment, _words_to_int, total_from_words


# ------------------------------- words to int -------------------------------
def test_spaced_indian_amount():
    assert _words_to_int("FOUR LAKH THIRTEEN THOUSAND THREE HUNDRED SIXTY SIX") == 413366


def test_hyphenated_tens():
    assert _words_to_int("FOUR LAKH SIXTY-EIGHT THOUSAND SEVEN HUNDRED THIRTY THREE") == 468733


def test_run_together_words_are_segmented():
    """Bharat's PDF emits no spaces at all."""
    assert _words_to_int("FOURLAKHTHIRTEENTHOUSANDTHREEHUNDREDSIXTYSIXRupees") == 413366


def test_crore_scale():
    assert _words_to_int("ONE CRORE TWENTY LAKH FIVE THOUSAND") == 12005000


def test_teens_are_not_split_into_smaller_words():
    """Greedy longest-match: 'thirteen' must not become 'three' + 'ten'."""
    assert _segment("thirteenthousand") == ["thirteen", "thousand"]
    assert _segment("sixtysix") == ["sixty", "six"]
    assert _segment("nineteen") == ["nineteen"]


def test_noise_words_are_ignored():
    assert _words_to_int("Rupees ONE THOUSAND ONLY") == 1000
    assert _words_to_int("TWO HUNDRED and FIFTY only") == 250


def test_bare_scale_words_imply_one():
    assert _words_to_int("LAKH") == 100000
    assert _words_to_int("HUNDRED") == 100


def test_unknown_words_give_up_rather_than_guess():
    assert _words_to_int("QWERTYASDF") is None
    assert _words_to_int("SUBJECT TO MUMBAI JURISDICTION") is None
    assert _words_to_int("") is None


# --------------------------------- extraction ---------------------------------
def test_reads_a_labelled_amount_in_words():
    text = "Total Amount in Words : FOUR LAKH THIRTEEN THOUSAND THREE HUNDRED SIXTY SIX ONLY"
    assert total_from_words(text) == "413366.00"


def test_reads_the_run_together_form():
    assert total_from_words("Total AmountinWords: FOURLAKHTHIRTEENTHOUSANDTHREEHUNDREDSIXTYSIXRupees") == "413366.00"


def test_keeps_the_largest_when_a_page_spells_out_more_than_one_figure():
    text = ("Tax Amount in Words : TWENTY THOUSAND NINE HUNDRED FIFTY THREE\n"
            "Total Amount in Words : FOUR LAKH THIRTEEN THOUSAND THREE HUNDRED SIXTY SIX")
    assert total_from_words(text) == "413366.00"


def test_returns_nothing_for_prose_next_to_a_label():
    assert total_from_words("Amount : subject to Mumbai jurisdiction only") is None


def test_returns_nothing_when_no_label_is_present():
    assert total_from_words("FOUR LAKH THIRTEEN THOUSAND") is None


def test_implausible_figures_are_rejected():
    """A misparse must become "no total", never a wrong total."""
    assert total_from_words("Total Amount in Words : " + "CRORE " * 20) is None
