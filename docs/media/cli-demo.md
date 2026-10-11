# CLI Demo Transcript

Captured on 2026-10-12 from a local checkout (`make install`) with
[examples/passage.txt](../../examples/passage.txt), 315 characters of original
English prose. `jq` trims the JSON reports to their key fields; the full
reports also include every ranked candidate, scores, and explanations.

The short-text Vigenère run at the end is included on purpose: on 60
characters the analyzer returns a wrong key and says why. See
[domain.md](../domain.md#limitations).

```console
$ codebreakers encrypt --cipher caesar --key 3 --text 'Attack at dawn'
Dwwdfn dw gdzq

$ codebreakers decrypt --cipher caesar --key 3 --text 'Dwwdfn dw gdzq'
Attack at dawn

$ codebreakers encrypt --cipher caesar --key 7 < examples/passage.txt | codebreakers analyze --analyzer caesar-bruteforce | jq '.candidates[0] | {key, explanation}'
{
  "key": "7",
  "explanation": "Shift 7 scored -31.4232 against english."
}

$ codebreakers encrypt --cipher vigenere --key LANTERN < examples/passage.txt | tee /tmp/cb_vig.txt | cut -c1-60
EHR DIVCPR BY XYR WITAXYBFSR PVFGP IA AMJ YZG ROIIL PVRGMET.

$ codebreakers analyze --analyzer vigenere-frequency < /tmp/cb_vig.txt | jq '{key_lengths: [.key_lengths[:3][] | .key_length], best_key: .candidate_keys[0].key}'
{
  "key_lengths": [
    7,
    1,
    12
  ],
  "best_key": "LANTERN"
}

$ cut -c1-60 /tmp/cb_vig.txt | codebreakers analyze --analyzer vigenere-frequency | jq '{best_key: .candidate_keys[0].key, limitations}'
{
  "best_key": "LENMERY",
  "limitations": [
    "Short ciphertexts may not contain enough repeated structure for reliable key recovery.",
    "Candidate keys are frequency-ranked hints, not guaranteed solutions."
  ]
}

$ codebreakers encrypt --cipher enigma --key 3 --text 'hello'; echo "exit status: $?"
Unsupported cipher 'enigma'. Supported ciphers: caesar, homophonic, substitution, vigenere.
exit status: 5
```

Errors go to standard error with a documented exit status (0–7, defined in
[exit_codes.py](../../src/codebreakers/cli/exit_codes.py)); 5 means
unsupported cipher.
