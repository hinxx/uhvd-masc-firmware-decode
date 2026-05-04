# UHVD MASC firmware decoder

See `AGENTS.md`.

This project was done with the help of Claude 4.7 and Codex 5.5.

Claude was able to figure out the coding schema; nibbles encoded as bytes with limited and specific code words for each position.
After several attempts at actually decoding the file Claude was failing to get anything done. It steared away from the right solution early on and chased deadends with high speculation. You can find some of its rumblings in the `junk` folder.

Once I focused on the "frame headers" (repetative sequences of 12 bytes) and extracted some knowledge out manually I presented the findings to Claude and Codex. Claude was suffering from its own misleadings (see `junk/prompt.tt` file), while Codex got it right immediately. Both were affected by the early
Clade blunder that 4 fixed bytes in the frame header should be decoded into `0xFFFF`. As soon as that was ignored, Codex understood that the last 4 bytes
in the frame headers is up counter. That completely identified 2 out of 4 nibble maps, and partially the rest. Another prompt to Codex to extrapolate
missing values was made and for the first time decoded bytes started to make sense.

At this time I can see ASCII commands (two characters) appearing in the decoded binary file. The vector table looks plausable. The leading string after the decode also makes sense (I doubt it can be decoded into anything more telling). With that I believe that the derived and extrapolated counter bijections are
correct and decoded bytes is valid MCU payload.
