# Domain Guide: Ciphers, Analysis, and Scoring

This guide explains what the cipher and cryptanalysis code does, how results
are scored, and where it stops working. The code is in
[`src/codebreakers/domain/`](../src/codebreakers/domain/). The design
decisions behind it are in
[ADR-0015](architecture/ADR-0015-cipher-and-analyzer-protocols.md),
[ADR-0004](architecture/ADR-0004-text-handling.md), and
[ADR-0006](architecture/ADR-0006-case-and-symbol-strategy.md).

> These are historical ciphers. Each one can be broken by hand or by this
> repository, so they give **no confidentiality**. Never use them to protect
> real data.

## Building blocks

| Concept | Type | Purpose |
| --- | --- | --- |
| Alphabet | `Alphabet` | An immutable, NFC-normalized sequence of unique symbols. The default is `A`–`Z`. Any symbol set works, for example `0123456789`. |
| Transform options | `TransformOptions` | Alphabet + `CaseStrategy` (`PRESERVE`, `UPPERCASE`, `LOWERCASE`, `IGNORE`) + `UnknownSymbolStrategy` (`PASS_THROUGH`, `STRIP`, `REJECT`). |
| Cipher | `Cipher[KeyT]` protocol | Stateless `encrypt(text, key, options)` and `decrypt(ciphertext, key, options)`. |
| Analyzer | `Analyzer[ResultT]` protocol | `analyze(text)` returns an explainable, versioned result. It never needs a key. |
| Language model | `LanguageModel` | A named, versioned table of expected symbol frequencies, currently `english` / `letter-frequency-v1`. |

All text passes through one pipeline in `domain/text.py`:

1. NFC normalization.
2. Alphabet lookup under the case strategy.
3. The cipher's index transformation.
4. Case reapplied, and unknown symbols passed through, stripped, or rejected.

Indexing is by Unicode code point, not grapheme cluster.

## Cipher catalogue

| Cipher | Key type | Example key | Key space (26 letters) | Behaviour |
| --- | --- | --- | --- | --- |
| Caesar | `int` shift | `3` | 26 | Shift every alphabet symbol by a fixed amount, modulo the alphabet length. Any integer is accepted. |
| Monoalphabetic substitution | `SubstitutionKey` | `QWERTYUIOPASDFGHJKLZXCVBNM` | 26! ≈ 4 × 10²⁶ | A one-to-one permutation of the alphabet. Incomplete or duplicate mappings are rejected. |
| Vigenère | `VigenereKey` | `LEMON` | 26^L for key length L | A repeating keyword of Caesar shifts. The keyword advances only on alphabet symbols, so spaces and punctuation do not consume key positions. |
| Homophonic substitution | `HomophonicKey` | `{"A":["11","12"],"B":["21"], …}` | Very large | Each plaintext symbol maps to one or more unique tokens. Encryption picks a token at random from an injected `random.Random`. Ciphertext is space-separated tokens, and decryption is deterministic. |

```bash
codebreakers encrypt --cipher caesar --key 3 --text "HELLO WORLD"            # KHOOR ZRUOG
codebreakers encrypt --cipher vigenere --key LEMON --text "ATTACK AT DAWN"    # LXFOPV EF RNHR
codebreakers encrypt --cipher caesar --key 2 --text "1239" --alphabet "0123456789"  # 3451
```

Round-trip correctness (`decrypt(encrypt(x)) == x`) is checked with Hypothesis
property tests for substitution, Vigenère, and homophonic ciphers. Caesar is
covered by example and boundary tests. Fixed regression vectors live in
[`tests/fixtures/historical_examples/v1/`](../tests/fixtures/historical_examples/v1/classical_ciphers.json),
each with recorded provenance and licence.

## Analysis capabilities

| Analyzer | Technique | Output | Solves automatically? |
| --- | --- | --- | --- |
| `caesar-bruteforce` | Decrypt with every shift and score each candidate against the language model. | All shifts, ranked, each with a score and explanation. | Yes, for ordinary English text of a sentence or more. |
| `vigenere-frequency` | Estimate the key length by the average index of coincidence of each column (lengths 1–12). Then solve each column as a Caesar shift by language score. | Ranked key lengths, up to five candidate keys with decrypted text, and stated limitations. | Often, with enough text (see [Limitations](#limitations)). |
| `substitution-frequency` | Count observed symbol frequencies and compare each with the language model. | Counts, frequencies, and per-symbol differences from English. | No. It assists a human solver. |
| `homophonic-distribution` | Count the distribution of space-separated tokens. | Token counts and frequencies. | No. It is a reporting aid. |

```bash
codebreakers analyze --analyzer caesar-bruteforce \
  --text "WKH TXLFN EURZQ IRA MXPSV RYHU WKH ODCB GRJ"
# rank 1: key "3", "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG"
```

Every result names its analyzer and language model version, so a stored
result can be traced to the scoring rules that produced it.

## Scoring

**Language score (chi-square).** For a candidate plaintext with *N* letters,
observed count *Oᵢ*, and expected frequency *pᵢ* for each letter *i*:

```text
score = −Σᵢ (Oᵢ − N·pᵢ)² / (N·pᵢ)
```

Higher (closer to zero) means more English-like. A score is relative: it
ranks candidates for the same ciphertext, and it is not a probability or a
confidence level. Symbols that are not in the model are ignored.

**Index of coincidence (IoC).** This is the probability that two symbols drawn
at random from the text are the same:

```text
IoC = Σᵢ nᵢ(nᵢ − 1) / (N(N − 1))
```

With the bundled frequencies, English scores about **0.065**, and uniformly
random letters score 1/26 ≈ **0.038**. Splitting Vigenère ciphertext into the
correct number of columns makes each column a Caesar cipher, which brings the
column IoC back towards the English value. That is the key-length signal.

## Limitations

These limitations are deliberate and documented, not hidden:

- **English only, single letters only.** The language model uses monogram
  frequencies. Bigram, trigram, and word models would rank short texts better.
- **Short Vigenère texts fail.** The benchmark ([benchmarks.md](benchmarks.md))
  recovers the 7-letter key `LANTERN` from 2,048 and 20,000 characters, but
  not from 128. Analysis requires at least 12 alphabet symbols and considers
  key lengths up to 12. Candidate keys are hints, not proofs.
- **Substitution and homophonic ciphertext are not solved.** No
  hill-climbing or simulated-annealing solver exists yet. These analyzers
  report statistics for a human to work from.
- **Scores are relative.** A top-ranked candidate is the best of the
  candidates tried, which does not make it correct. Treat very short or
  non-English input with suspicion.
- **Code points, not graphemes.** Combined characters that do not compose
  under NFC count as several symbols.
- **Bounded input.** The HTTP API caps text at 20,000 characters, and each
  queued analysis runs under a 30 s time budget (ADR-0007, ADR-0009).

## Historical sources

- Caesar cipher: Suetonius, *Life of Julius Caesar* 56.6, which describes
  substituting "the fourth letter of the alphabet, namely D, for A".
  [LacusCurtius translation](https://penelope.uchicago.edu/Thayer/E/Roman/Texts/Suetonius/12Caesars/Julius*.html).
  Overview: [Caesar cipher](https://en.wikipedia.org/wiki/Caesar_cipher).
- Frequency analysis: first described by [Al-Kindi](https://en.wikipedia.org/wiki/Al-Kindi)
  in the 9th century. See [Frequency analysis](https://en.wikipedia.org/wiki/Frequency_analysis).
- Vigenère cipher: described by
  [Giovan Battista Bellaso](https://en.wikipedia.org/wiki/Giovan_Battista_Bellaso)
  (1553) and later misattributed to Blaise de Vigenère.
  See [Vigenère cipher](https://en.wikipedia.org/wiki/Vigen%C3%A8re_cipher).
- Breaking Vigenère: the
  [Kasiski examination](https://en.wikipedia.org/wiki/Kasiski_examination) (1863),
  and William F. Friedman's
  [index of coincidence](https://en.wikipedia.org/wiki/Index_of_coincidence) (1922),
  which this project uses.
- Homophonic substitution:
  [Substitution cipher: homophonic](https://en.wikipedia.org/wiki/Substitution_cipher#Homophonic).
  Historical examples include the [Great Cipher](https://en.wikipedia.org/wiki/Great_Cipher)
  of Louis XIV and the [Copiale cipher](https://en.wikipedia.org/wiki/Copiale_cipher).
- Chi-square statistic: [Chi-squared test](https://en.wikipedia.org/wiki/Chi-squared_test).

The regression fixtures are constructed or commonly taught examples, licensed
CC0 or public domain. No copyrighted historical transcriptions are bundled.
