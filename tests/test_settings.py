"""The options of one run (``ebook_processor/settings.py``): what ``from_options`` makes of the reader's choices, what
``validate`` refuses and what it says, and what is saved with a run and loaded again."""

from __future__ import annotations

import dataclasses

import pytest

from ebook_processor.settings import DENSITIES, EFFORTS, MAX_QUESTIONS, Settings, SettingsError, from_options


def test_the_defaults_are_a_medium_density_with_open_questions():
    settings = from_options()
    assert settings == Settings()
    assert (settings.min_words, settings.max_words) == DENSITIES["medium"] == (600, 2000)
    assert (settings.max_questions, settings.content, settings.kind, settings.rounds, settings.jobs) == (3, "text", "auto", 3, 3)
    assert (settings.model, settings.checker_model, settings.effort) == (None, None, None)
    assert settings.part_words == 10000 and settings.epubcheck is False and settings.preview is False
    assert settings.forms == ("open",) and settings.open_only and settings.title_suffix == " (checkpoints)"


def test_an_option_that_is_not_given_keeps_its_default():
    """The command line and the Add form pass ``None`` for what the reader left alone."""
    assert from_options(density=None, section_words=None, max_questions=None, model=None, forms=None, jobs=None) == Settings()
    assert from_options(rounds=0).rounds == 0 and from_options(epubcheck=False).epubcheck is False  # given, though false


@pytest.mark.parametrize("density", list(DENSITIES))
def test_a_density_sets_the_length_of_a_section(density):
    settings = from_options(density=density)
    assert (settings.min_words, settings.max_words) == DENSITIES[density]


def test_a_density_that_does_not_exist_is_refused():
    with pytest.raises(SettingsError, match="^density must be one of short, medium, long$"):
        from_options(density="tiny")


def test_a_section_length_in_words_wins_over_the_density():
    settings = from_options(density="short", section_words="400-1200")
    assert (settings.min_words, settings.max_words) == (400, 1200)
    assert from_options(section_words=" 400 - 1200 ").max_words == 1200  # spaces around the numbers do no harm


@pytest.mark.parametrize("text", ["400", "400-", "-1200", "four-ten", "400-1200-2000", "400 to 1200", "4.5-9"])
def test_a_section_length_that_is_not_two_numbers_is_refused(text):
    with pytest.raises(SettingsError) as said:
        from_options(section_words=text)
    assert str(said.value) == f"section length {text!r}: write it as MIN-MAX, for example 600-2000"


def test_a_part_holds_at_least_two_sections_unless_the_reader_set_its_size():
    assert from_options(density="long").part_words == 10000              # two sections of 4000 words fit as it is
    assert from_options(section_words="3000-5000").part_words == 10000   # and two of 5000, exactly
    assert from_options(section_words="3000-5001").part_words == 15003   # too small for two: three times the longest
    assert from_options(section_words="3000-8000").part_words == 24000
    assert from_options(section_words="3000-8000", part_words=16000).part_words == 16000   # the reader's own number is kept
    with pytest.raises(SettingsError, match="^part size must be at least twice the longest section$"):
        from_options(section_words="3000-8000", part_words=15999)


@pytest.mark.parametrize("options, said", [
    ({"section_words": "40-1000"}, "section length 40-1000 words: need 50 <= min < max <= 20000"),
    ({"section_words": "1000-1000"}, "section length 1000-1000 words: need 50 <= min < max <= 20000"),
    ({"section_words": "2000-600"}, "section length 2000-600 words: need 50 <= min < max <= 20000"),
    ({"section_words": "600-20001", "part_words": 60000}, "section length 600-20001 words: need 50 <= min < max <= 20000"),
    ({"max_questions": 0}, f"the most questions per checkpoint must be 1 to {MAX_QUESTIONS}"),
    ({"max_questions": MAX_QUESTIONS + 1}, f"the most questions per checkpoint must be 1 to {MAX_QUESTIONS}"),
    ({"content": "pictures"}, "content must be one of prose, text, figures"),
    ({"kind": "poetry"}, "kind must be one of auto, expository, narrative, technical"),
    ({"rounds": -1}, "rounds must be 0 to 5"),
    ({"rounds": 6}, "rounds must be 0 to 5"),
    ({"effort": "extreme"}, "effort must be one of " + ", ".join(EFFORTS)),
    ({"jobs": 0}, "jobs must be 1 to 8"),
    ({"jobs": 9}, "jobs must be 1 to 8"),
    ({"part_words": 1999, "section_words": "100-400"}, "part size must be 2000 to 60000 words"),
    ({"part_words": 60001}, "part size must be 2000 to 60000 words"),
    ({"part_words": 3999}, "part size must be at least twice the longest section"),
])
def test_each_limit_is_refused_with_its_message(options, said):
    with pytest.raises(SettingsError) as error:
        from_options(**options)
    assert str(error.value) == said


@pytest.mark.parametrize("options", [
    {"section_words": "50-20000", "part_words": 40000}, {"max_questions": 1}, {"max_questions": MAX_QUESTIONS},
    {"rounds": 0}, {"rounds": 5}, {"jobs": 1}, {"jobs": 8}, {"part_words": 4000}, {"part_words": 60000},
    {"section_words": "100-400", "part_words": 2000}, {"content": "prose"}, {"content": "figures"},
    {"kind": "narrative"}, {"effort": "max"},
])
def test_the_values_at_each_limit_are_taken(options):
    settings = from_options(**options)
    for name, value in options.items():
        if name != "section_words":
            assert getattr(settings, name) == value
    assert Settings.from_json(settings.as_json()) == settings


def test_the_forms_are_tidied_by_from_options_and_checked_by_validate():
    assert from_options(forms="all").forms == ("open", "choice", "truefalse", "cloze")
    assert from_options(forms="cloze, open").forms == ("open", "cloze")            # the fixed order, from a text
    assert from_options(forms=["truefalse", "choice", "choice"]).forms == ("choice", "truefalse") and not from_options(forms="choice").open_only
    with pytest.raises(SettingsError, match="^unknown form of question: essay "):
        from_options(forms="open,essay")
    with pytest.raises(SettingsError, match="^allow at least one form of question$"):
        from_options(forms="")
    with pytest.raises(SettingsError, match="^the forms of question are a list of: "):
        from_options(forms=3)
    # settings made without from_options are held to the same order by validate
    with pytest.raises(SettingsError, match="^the forms of question are given in this order, each once: open, choice, truefalse, cloze$"):
        Settings(forms=("cloze", "open")).validate()
    with pytest.raises(SettingsError, match="^the forms of question are given in this order, each once: "):
        Settings(forms=("open", "open")).validate()


def test_an_option_that_does_not_exist_is_an_error_of_the_caller():
    with pytest.raises(TypeError):
        from_options(colour="red")


def test_settings_cannot_be_changed_once_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        from_options().rounds = 0  # type: ignore[misc]


def test_what_is_saved_with_a_run_is_loaded_again_as_the_same_settings():
    settings = from_options(section_words="400-1200", max_questions=5, content="figures", kind="technical", rounds=1,
                            model="small", checker_model="codex:gpt", effort="high", jobs=2, part_words=5000,
                            epubcheck=True, preview=True, forms="open,cloze", ephemeral="linked", ephemeral_forms="choice,truefalse")
    saved = settings.as_json()
    assert saved == {"min_words": 400, "max_words": 1200, "max_questions": 5, "content": "figures", "kind": "technical",
                     "rounds": 1, "model": "small", "checker_model": "codex:gpt", "effort": "high", "jobs": 2,
                     "part_words": 5000, "epubcheck": True, "title_suffix": " (checkpoints)", "preview": True,
                     "forms": ["open", "cloze"], "ephemeral": "linked",
                     "ephemeral_forms": ["choice", "truefalse"]}   # lists, as JSON has no tuple
    assert Settings.from_json(saved) == settings and Settings.from_json(saved).forms == ("open", "cloze")
    assert Settings.from_json(saved).ephemeral_forms == ("choice", "truefalse")
    assert set(saved) == {f.name for f in dataclasses.fields(Settings)}  # every option is saved


def test_loading_fills_in_what_an_older_run_did_not_save_and_ignores_what_it_does_not_know():
    old = Settings.from_json({"min_words": 300, "max_words": 1000, "an_option_of_a_later_version": 7})
    assert (old.min_words, old.max_words) == (300, 1000) and old == from_options(density="short")
    assert Settings.from_json({}) == Settings()


def test_the_forms_of_ephemeral_questions_are_a_setting_of_their_own():
    settings = from_options(forms="open", ephemeral="attention", ephemeral_forms="cloze, choice")
    assert settings.forms == ("open",) and settings.ephemeral_forms == ("choice", "cloze")   # tidied as the forms are
    assert settings.open_only and settings.all_forms == ("open", "choice", "cloze")
    assert Settings().ephemeral_forms == ("open",)                                   # open only, unless the reader ticks others
    # without ephemeral questions they play no part
    plain = from_options(forms="open", ephemeral_forms="choice")
    assert plain.all_forms == ("open",)
    with pytest.raises(SettingsError, match="^ephemeral questions: unknown form of question: essay "):
        from_options(ephemeral_forms="essay")
    with pytest.raises(SettingsError, match="^ephemeral questions: allow at least one form of question$"):
        from_options(ephemeral_forms="")
    with pytest.raises(SettingsError, match="^the forms of an ephemeral question are given in this order, each once: "):
        Settings(ephemeral_forms=("cloze", "open")).validate()


def test_a_run_saved_before_the_ephemeral_questions_had_forms_of_their_own_wrote_them_in_the_forms_of_the_book():
    """Such a run's settings have ``ephemeral`` but no ``ephemeral_forms``: its ephemeral questions took ``forms``."""
    old = {**from_options(forms="open,choice", ephemeral="attention").as_json()}
    del old["ephemeral_forms"]
    loaded = Settings.from_json(old)
    assert loaded.ephemeral_forms == ("open", "choice") and loaded.forms == ("open", "choice")
    # so it resumes with the same choice made in both places, and not with the new default
    assert loaded.drafting_key() == from_options(forms="open,choice", ephemeral="attention", ephemeral_forms="open,choice").drafting_key()
    assert loaded.drafting_key() != from_options(forms="open,choice", ephemeral="attention").drafting_key()
    older = Settings.from_json({"forms": ["choice"]})                              # from before ephemeral questions
    assert older.ephemeral_forms == ("open",) and older.drafting_key() == from_options(forms="choice").drafting_key()


def test_loading_checks_the_limits_again():
    with pytest.raises(SettingsError, match="^rounds must be 0 to 5$"):
        Settings.from_json({**Settings().as_json(), "rounds": 9})
    with pytest.raises(SettingsError, match="^the forms of question are given in this order"):
        Settings.from_json({**Settings().as_json(), "forms": ["cloze", "open"]})
    # loading does not make a part larger, as from_options does: what was saved is what was run
    with pytest.raises(SettingsError, match="^part size must be at least twice the longest section$"):
        Settings.from_json({"min_words": 1500, "max_words": 8000})


def test_the_drafting_key_holds_only_what_changes_the_draft():
    """A run can be continued with another model or more checks, but not with other sections or forms."""
    first = from_options(density="short")
    assert first.drafting_key() == {"min_words": 300, "max_words": 1000, "max_questions": 3, "content": "text",
                                    "kind": "auto", "part_words": 10000, "forms": ["open"],
                                        "ephemeral": "none"}
    same = from_options(density="short", model="small", checker_model="other", effort="low", rounds=0, jobs=1, epubcheck=True, preview=True)
    assert same.drafting_key() == first.drafting_key()
    for change in ({"density": "medium"}, {"density": "short", "max_questions": 2}, {"density": "short", "content": "prose"},
                   {"density": "short", "kind": "narrative"}, {"density": "short", "part_words": 4000}, {"density": "short", "forms": "all"}):
        assert from_options(**change).drafting_key() != first.drafting_key(), change
    # the forms of the ephemeral questions count only in a book that has them
    assert from_options(density="short", ephemeral_forms="all").drafting_key() == first.drafting_key()
    with_them = from_options(density="short", ephemeral="linked")
    assert with_them.drafting_key() == {**first.drafting_key(), "ephemeral": "linked", "ephemeral_forms": ["open"]}
    assert from_options(density="short", ephemeral="linked", ephemeral_forms="choice").drafting_key() != with_them.drafting_key()
