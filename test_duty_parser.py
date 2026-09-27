"""Tests for matching duty names in an event's detail blob."""
import unittest

from duty_parser import find_duty_lines, normalise

BLOB = """Coach: Anna Keller Tel. 079 111 22 33
Betreuer: Marc Weber Tel. 079 444 55 66
2 Pack Farmer/Riegel bringt mit: Fam. Smith

PLO
Reporter: Lena Vogt (Lenchen)
Speaker: John Doe
Zeit: Fam. Brown
Strafbank: Smith / Green
Kuchenbuffet: Hofer / Meier"""


class Normalise(unittest.TestCase):
    def test_strips_accents_and_casefolds(self):
        self.assertEqual(normalise("César"), "cesar")
        self.assertEqual(normalise("CÉSAR"), "cesar")
        self.assertEqual(normalise("cesar"), "cesar")

    def test_leaves_plain_text_alone(self):
        self.assertEqual(normalise("Smith"), "smith")


class FindDutyLines(unittest.TestCase):
    def test_matches_first_last_form(self):
        self.assertEqual(find_duty_lines(BLOB, ["John Doe"]), ["Speaker: John Doe"])

    def test_matches_fam_prefix_form(self):
        self.assertEqual(find_duty_lines(BLOB, ["Brown"]), ["Zeit: Fam. Brown"])

    def test_matches_slash_separated_surnames(self):
        self.assertEqual(find_duty_lines(BLOB, ["Green"]), ["Strafbank: Smith / Green"])

    def test_one_name_can_match_several_lines(self):
        self.assertEqual(
            find_duty_lines(BLOB, ["Smith"]),
            ["2 Pack Farmer/Riegel bringt mit: Fam. Smith", "Strafbank: Smith / Green"],
        )

    def test_matches_last_comma_first_form(self):
        blob = "timekeeper: doe, jane"
        self.assertEqual(find_duty_lines(blob, ["Doe"]), ["timekeeper: doe, jane"])

    def test_is_case_and_accent_insensitive(self):
        blob = "Speaker: CESAR Moreno"
        self.assertEqual(find_duty_lines(blob, ["César"]), ["Speaker: CESAR Moreno"])

    def test_respects_word_boundaries(self):
        blob = "Speaker: Alan Smithson"
        self.assertEqual(find_duty_lines(blob, ["Smith"]), [])

    def test_absent_name_matches_nothing(self):
        self.assertEqual(find_duty_lines(BLOB, ["Nobody"]), [])

    def test_blank_lines_and_headers_never_match(self):
        self.assertEqual(find_duty_lines(BLOB, ["PLO"]), ["PLO"])
        self.assertEqual(find_duty_lines("\n\n   \n", ["Smith"]), [])

    def test_several_names_return_lines_in_document_order(self):
        self.assertEqual(
            find_duty_lines(BLOB, ["Brown", "John Doe"]),
            ["Speaker: John Doe", "Zeit: Fam. Brown"],
        )

    def test_a_line_matching_two_names_appears_once(self):
        self.assertEqual(
            find_duty_lines(BLOB, ["Smith", "Green"]),
            ["2 Pack Farmer/Riegel bringt mit: Fam. Smith", "Strafbank: Smith / Green"],
        )

    def test_empty_inputs_are_safe(self):
        self.assertEqual(find_duty_lines("", ["Smith"]), [])
        self.assertEqual(find_duty_lines(BLOB, []), [])
        self.assertEqual(find_duty_lines(None, ["Smith"]), [])

    def test_returned_lines_are_stripped(self):
        self.assertEqual(find_duty_lines("   Speaker: John Doe   ", ["John Doe"]),
                         ["Speaker: John Doe"])


if __name__ == "__main__":
    unittest.main()
