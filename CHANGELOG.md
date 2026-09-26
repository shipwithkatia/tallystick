# Changelog

## Unreleased

Two pieces of dead code a linter cannot see, because neither is local to a
function: `Entry.amount`, a field no reader or writer ever touched and that
`io.py` does not even read from a trace file, and `check`, a module-level
alias of `check_trace` whose own comment said it read better inside the
module - which used it nowhere. Both removed.

## v0.9.0 — the quote check asks where a quote was cut

The previous tag was `v0.7.4`. The numbers v0.7.5, v0.8.0 and v0.8.1 reached
`main` without a tag of their own; v0.8.2 to v0.8.4 were working versions of
this same change and never reached `main` at all. Which of the three each
number was is in [`bench/HISTORY.md`](bench/HISTORY.md#versions).

### What changed

tallystick checks every evidence entry against its source. Before this release,
that check compared two strings: the quote the trace carries and the text at the
address it gives. That comparison cannot see one kind of forgery. A trace is free
to declare its span one character to the right, and then

    source    The report found that 66 300 people were affected
    span      starts one character after "66 "
    quote     300 people were affected

matches its span word for word. The quote says 300; the source says 66 300.

v0.9.0 asks where the span was cut. It reads the number or the word at each
boundary whole — sign, currency, decimal point, grouping space or comma, share
and degree signs, brackets — and refuses a span whose boundary falls inside it.
What it does not read as part of a number or a word, it does not protect; the
shapes of that kind found so far are under Known limitations below, and that
list is not complete.
It also names what a quote may differ by (whitespace, the comma, the two
quotation marks, a sentence mark at either end) instead of naming what it may not.
Both sides are read the same way first, and that reading forgives more: letter
case (`Polish` cited as `polish`), the kind of dash (`–`, `—` and the minus sign
`−` read as `-`), curly and angle quotation marks and primes, Unicode
composition and six invisible characters. No other difference is on either list.

### Forgeries now refused that v0.8.1 accepted

Each of these closed the books at exit 0 on v0.8.1:

- a sign dropped: `+5%` cited as `-5%`, `-$5.2 million` cited as `$5.2 million`,
  `-.5%` as `.5%`, `~$3` as `$3`, `≤$5` as `$5`, `>50` as `50`. One space breaks
  the second example: `- $5.2 million` at the start of a line cited as
  `$5.2 million` is still accepted, because a dash and a space that open a line
  are read as a list bullet;
- a currency dropped: `$5 million` cited as `5 million`;
- a sign standing one space from its number in the middle of a line:
  `margin - 5.2 % lower` cited as `margin 5.2 % lower`;
- a span cut inside a number: `66 300` or `66,300` cited as `300`, `.5%` as `5%`,
  `1/2` as `2`, `$100-$300` as `$300`;
- a span cut inside a word: `unsafe` cited as `safe`;
- a word boundary moved: `notable` cited as `not able`.

### Measured

On `bench/quote_gate_corpus.py`, with pairs built from the 5,615 real quotes of
the two published benchmark runs:

| | v0.8.1 | v0.9.0 |
|---|---|---|
| tampering pairs refused | 25.23% | 100% of 305,499 |
| typographic pairs accepted | 93.84% | 100% of 284,010 |
| spans cut inside a number, refused | 0% (not asked) | 100% of 194,321 |
| spans cut inside a word, refused | 0% (not asked) | 100% of 415,886 |

On v0.8.1 the boundary was never asked: a quote identical to its span was
accepted before any rule ran.

### What it costs

The price is false alarms: honest quotes refused.

| | v0.8.1 | v0.9.0 |
|---|---|---|
| real quotes of the two runs refused (all exact slices) | 0 of 5,615 | 8 of 5,615 |
| values of real tool output refused | 0 of 1,044 | 75 of 1,044 (7.18%) |
| the same values reprinted as compact JSON, refused | 0 of 529 | 117 of 529 (22.12%) |
| claim statuses changed, 424 posted traces | — | 1 of 11,547 |
| published benchmark rows changed | — | 0 |

Of the 8 real quotes, one is a real cut (`1/2` cited as `2`) and seven are the
named price of the rule. The 75 tool-output values are members of 11 lists of
numbers written with no space after the comma, all in one trajectory.

The published benchmark numbers (F1 0.59 against 0.58 and 0.05; false alarms on
7% of clean sentences against 16%; the AgentHallu tables) were measured on v0.6
and v0.7.3. The model sides were not run again. The audit side was: re-auditing
the 424 posted files on v0.9.0 reproduces all 580 sentence flags and all 225
trajectory scores that are committed under `bench/results/`.

**Time.** Checking the 424 posted traces (`verify_run` alone, best of five runs, one machine) takes 0.48 s on v0.9.0 against 0.052 s on v0.8.1: about nine times slower, about 1.1 ms a trace. v0.8.1 answered almost every quote by string equality; v0.9.0 reads both boundaries of every quote.

### Known limitations

Each is in the README under Known limitations, with an example, why it is
open and, where it was counted, its frequency in the corpus. The list is what
the external reviews had found by 25 September 2026. It is not complete: each
review found shapes the one before it had not, and the next will most likely
find more. v0.9.0 refuses what these notes describe as refused; anything else,
it may accept.

Forgeries still accepted:

- the left end of a range written with spaces: `$3.74 - $4.83` cited as `$3.74`;
- a dash with a space or a line break after a number: `pp. 1119– 1190` cited
  as `1119`;
- a dash and a space at the start of a line: `- $5.2 million` cited as
  `$5.2 million`, `- 5.2%` as `5.2%`;
- a sign before a bracket: `U = −(3/5)` cited as `(3/5)`;
- a currency before a bracket: `$(5.2) million` cited as `(5.2) million`;
- a word run into a number: `Windows10` cited as `10`;
- a cut at a capital, an underscore or an escape inside a word: `notFound` as
  `Found`, `not_found` as `found`, `MacArthur` as `Arthur`, `C:\nuclear` as
  `uclear`;
- a mark after a number: a trailing minus `1,234.56-` as `1,234.56`, a prime
  `4″` as `4`, an operator `5 ^ 2`, `5 +/- 0.2` or `5 ×10` as `5`;
- a symbol before a number: `▼5.2%` as `5.2%`;
- a mark before a word: `!ready` cited as `ready`, `x != 5` as `= 5`, the
  removed diff line `-enable_ssl = true` as `enable_ssl = true`, `-inf` as
  `inf`, `~mutable` as `mutable`, `¬valid` as `valid`. The rule reads a number
  with the marks that change its value and a word as its letters only;
- shapes the corpus does not contain: `↓5.2%` as `5.2%`, `+/-0.5` as `-0.5`,
  `10**3` as `10`, `:30` as `30`, `&minus;5` as `5`;
- the mixed fraction `5 1/2` cited as `5` or `1/2`;
- a size written as a word: `$5 million` cited as `$5`.

Honest quotes refused:

- a member of a list of numbers with no space after the comma: `3,4,7` as `4`;
- two numbers one space apart, the second of three digits: `GET /api 200 512`,
  `HTTP/2 200`, pandas columns;
- a spaced dash before a number mid-line: Python logging (`- INFO - 42 rows`),
  `Report - 2023`, `Census 2011 - 2.6 %`;
- a literal escape before a number (`\xa0`, `\u200a`, a control character),
  from output saved through `repr()`;
- a version: `v1.2.3` as `1.2.3`;
- part of a timestamp or an address: the date of `2025-08-27T00:00:00Z`, the
  port of `192.168.1.1:8080`;
- a full stop, hyphen or bracket a recorder dropped: `U.S.` as `US`.

Not attempted: the check verifies where a quote came from and where it was cut,
not what it means. An exact quote can still mislead through a pronoun, a dropped
condition or a connective.

The instrument: 150 of the 270 cells of its table of marks beside a numeral have
no example in the corpus and agree with the rule by construction; four changes to
the rule pass both the instrument and the tests.

### Also in this release

- The model-side proposer asks the gate's question about a number of a quote
  before placing it, so it no longer places a span the gate refuses for cutting
  a number. It does not ask the question about a word: of the 5,615 real
  quotes it places 5,611, and the gate refuses 4 of those for cutting a word —
  four of the seven named prices above (`structure—there` twice,
  `2,883Medal`, `1811The`). Given
  `safe for children` from `unsafe for children`, it places `safe for children`
  and the gate refuses it.
- A long word beside a quote (a 1,000,000-character base64 image) no longer
  slows the check: the boundary reads at most 256 characters into a word.
- The proposer again places an exact quote that opens on an ASCII minus before
  a currency or a point (`-$5.2 million`), which an intermediate version had
  stopped placing. Only the exact quote. When the model adds a full stop or
  quotation marks, the proposer looks for the quote by its words and drops any
  mark at its edge that is not part of a word to that search. A quote that
  opens on a dash before a currency or a point, or on a dash before a second
  dash and a digit (`-$5`, `–$5`, `(-$5`, `-.5`, `--5`), is then placed
  nowhere, and so is one that opens on a dash, a space and a number in the
  middle of a line (`- 2023 file`). A quote whose edge mark the gate does not
  protect is placed without it, and the books close at exit 0:
  `!ready then abort now.` as `ready then abort now`, the removed diff line
  `-enable_ssl = true.` as `enable_ssl = true`. Of 31 forms tried, 15 are
  placed that way (21 before the change in the next item). The same happens to a dash before a space or a second dash and a
  word: `- Paris is big.` in the middle of a line is placed as `Paris is big`,
  `--no-verify now.` as `no-verify now`. The README describes both as a class under Known limitations; the
  forms tried are not all there are.
- The proposer no longer places the model's `-$5 million` on a source's
  `$5 million`. From v0.6.0, when the model's quote was not in the source as it
  stands, the word search treated a dash as punctuation: the source said
  `a loss of $5 million`, the model quoted `-$5 million`, and the proposer
  placed `$5 million` and recorded it as the model's quote. The audit then
  closed the books, since that text really is at its address. A span the word
  search finds is now placed only if the text it writes states the same signs
  and digits as the quote, as the gate reads a number; a sign added, dropped
  or reversed that the reading sees means no span. Of the forms tried, a
  trailing minus, accounting brackets, a dash glued to a square or a curly
  bracket (`"-[5] now here"` placed as `5] now here`) and a dash a space away
  from a bracket (`"- (5%) this year"` placed as `(5%) this year`) are
  outside that reading and outside this check.
  The same check stops six of the 31 forms above from being placed without
  their sign (`- $5.2 million lost.` at the start of a line, `"-(5%) this
  year"` with the dash glued to a round bracket); they are now placed nowhere.
  Cost, measured on the 5,615 real quotes of both published runs: 0 of them
  placed differently. They are exact slices, and all but 4 are placed before
  the word search is reached; those 4 are exact hits that cut a number, and
  neither version places them. So this measures little. With a full stop
  added to each, or quotation marks
  around each, 5 of 5,615 are no longer placed: quotes that open on a list
  bullet before a year (`- 2017: Vittoria Colizza`). Read alone, a quote's
  opening `- ` cannot be told from a sign, so the proposer places nothing and
  logs it. Before, it placed them without the bullet. 0 of 1,044 values of
  real tool output, 0 of 11,547 claim statuses and 0 published rows moved; the
  audit side does not call the proposer. These prices cover the exact search
  and the word search. A quote into the model's own intermediate text is then
  written clipped to the claims it falls in, which can drop a dash the quote
  held; that third route was not measured in any earlier round (README, Known
  limitations).

1141 tests pass; 12 are expected failures, each a named limitation.
