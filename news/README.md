# news

One file per change, named `<short-summary>.<kind>` where `<kind>` is `major`, `minor` or
`fix`. The text is the release note: one line, in the present tense, saying what changed
for someone using this — not how it was implemented.

    echo "Refuse a fragment with no text, and say what to write." > news/empty-fragment.fix

Merging spends them: the release workflow reads them, groups them by kind into the release
notes, and deletes them.
