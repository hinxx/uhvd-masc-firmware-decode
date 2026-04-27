# MC56F8345 Firmware Decoding Analysis

## Summary

I analyzed the Applied Motion Products firmware update file for the NXP/Freescale MC56F8345 DSP microcontroller. Here are the key findings:

## File Structure Discovered

### 1. Header (13 bytes)
- ASCII version string: `1.07R,22\r\n` (10 bytes)
- 3-byte preamble: `80 af a9` (likely decoding key/state)

### 2. Payload Structure (192,740 bytes)
- **Framed transmission format** with 168-byte frames
- **Frame marker**: `99 56 87 68 9c 66 1b a5` (8 bytes) appears 1,146 times
- **Frame content**: 160 bytes of encoded data per frame
- **Pre-marker data**: 40 bytes before first frame
- **Total clean payload**: 183,572 bytes (after removing frame markers)

### 3. Encoding Ratio
- **2:1 encoding ratio**: 4 encoded bytes → 1 decoded 16-bit word (2 bytes)
- Expected decoded size: ~91,786 bytes (fits in 128KB MCU flash)
- Vector table: 81 entries × 4 bytes = 324 decoded bytes (648 encoded bytes)

## Encoding Analysis

### 1. Position-Dependent Nibble Constraints
Each byte position (mod 4) has restricted high nibbles:
- **Position 0**: `{8, 9, E, F}` (4 values) → likely encodes 2 bits
- **Position 1**: `{1, 2, 5, 6}` (4 values) → likely encodes 2 bits  
- **Position 2**: `{1, 2, 6, 8, F}` (5 values, but 1,6,8,F primary)
- **Position 3**: `{1, 6, 9, A, D}` (5 values, but 1,6,A,D primary)

Low nibbles use most/all 16 possible values → likely obfuscation or carry additional data.

### 2. Transmission Framing
- The 168-byte frame size suggests a serial transmission protocol
- Each frame contains ~40 decoded words (80 flash bytes)
- Frame markers may include sequence numbers or checksums

### 3. Common Patterns
Most frequent 4-byte encoded patterns (represent single 16-bit words):
- `fa558269`: 1,694 times
- `9c661ba5`: 1,583 times  
- `8e548b6d`: Should decode to JSR opcode `54 e2` based on analysis
- `99626ea7`: Likely JSR target address

## Decoding Attempts

I tried multiple decoding methods but haven't found the exact algorithm:

### 1. Simple XOR Methods
- Fixed XOR with preamble bytes (`80`, `af`, `a9`)
- Rotating XOR with preamble sequence
- Position-based XOR

### 2. Stateful Methods
- Running XOR (each byte XORs with previous)
- Running sum/difference decoding
- State machine approaches

### 3. Bit-Level Methods
- Nibble extraction and recombination
- Bit interleaving/deinterleaving
- Manchester-style decoding

### 4. Pattern-Based Methods
- High nibble → 2-bit data mapping
- Byte pair combinations
- Position-dependent lookup tables

## Key Insights

1. **The encoding is sophisticated** - not a simple XOR or substitution
2. **Position-dependent structure** strongly suggests a designed encoding scheme
3. **Frame structure indicates serial protocol** - likely RS-232 or similar with error detection
4. **Applied Motion Products proprietary format** - appears custom, not standard hex/SREC

## Files Generated

1. `firmware_analyzer.py` - Extracts frame structure and clean payload
2. `firmware_elf_clean.bin` - De-framed payload data (183,572 bytes)
3. `advanced_decoder.py` - Tests multiple decoding approaches
4. `bit_decoder.py` - Bit-level analysis and pattern detection
5. `final_decoder.py` - Nibble-based decoding attempt
6. `firmware_elf_clean_final_decoded.bin` - Best decode attempt (91,786 bytes)

## Next Steps

To fully crack this encoding, you would likely need:

1. **Known plaintext** - Another firmware version with slight differences
2. **Bootloader source** - Applied Motion Products' update tool source code
3. **Protocol documentation** - Details on their proprietary format
4. **Differential analysis** - Multiple firmware versions to find patterns
5. **MCU dumps** - Extract actual flash contents to compare with update files

The encoding appears to be a custom obfuscation/error-detection scheme rather than simple encryption. The position-dependent nibble constraints suggest it may be based on error-correcting codes or line codes designed for reliable serial transmission.

## Usage

```bash
# Extract clean payload without frame markers
python firmware_analyzer.py firmware_elf.e

# Try various decoding methods  
python advanced_decoder.py firmware_elf_clean.bin

# Bit-level analysis
python bit_decoder.py firmware_elf_clean.bin

# Final decode attempt
python final_decoder.py firmware_elf_clean.bin
```

The clean payload file makes it much easier to experiment with different decoding algorithms without worrying about the framing structure.
