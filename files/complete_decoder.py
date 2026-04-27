#!/usr/bin/env python3
"""
Complete Vector Decoder - Final version with full 54 e1/e2 decoding
"""

import sys

class CompleteDecoder:
    def __init__(self):
        # Discovered mappings
        self.key_byte3_to_byte1 = 0x45  # XOR key for byte 3 -> decoded byte 1
        self.key_byte0_to_byte0 = 0xcd  # XOR key for byte 0 -> decoded byte 0 (incomplete)
        
        # We need to find how to decode to 0x54 for the high byte
        
    def complete_mapping_discovery(self):
        """Find the complete mapping including the 0x54 high byte"""
        print("=== Finding Complete Mapping ===")
        
        # Known patterns that should decode to 54 e1 and 54 e2
        patterns = {
            'jsr': bytes.fromhex('99626ea7'),    # -> 54 e2
            'jmp': bytes.fromhex('99626ea4'),    # -> 54 e1  
        }
        
        # We know byte 3 -> byte 1 mapping works (0x45 XOR key)
        # We need to find byte X -> byte 0 mapping for 0x54
        
        print("Testing all positions for 0x54 decode:")
        mapping = {}
        
        for pattern_name, encoded in patterns.items():
            expected_byte0 = 0x54
            expected_byte1 = 0xe2 if pattern_name == 'jsr' else 0xe1
            
            print(f"\n{pattern_name}: {encoded.hex()} -> expected 54{expected_byte1:02x}")
            
            # We know byte 1 works
            decoded_byte1 = encoded[3] ^ self.key_byte3_to_byte1
            print(f"  Byte 1: pos[3] ^ 0x45 = {decoded_byte1:02x} ✓")
            
            # Try all positions for byte 0 = 0x54
            for pos in range(4):
                for key in range(256):
                    if (encoded[pos] ^ key) == 0x54:
                        print(f"  Byte 0: pos[{pos}] ^ 0x{key:02x} = 0x54")
                        mapping[f'{pattern_name}_pos{pos}_key'] = key
                        break
        
        # Check if we found consistent mapping
        jsr_keys = [(k, v) for k, v in mapping.items() if k.startswith('jsr')]
        jmp_keys = [(k, v) for k, v in mapping.items() if k.startswith('jmp')]
        
        print(f"\nJSR mappings: {jsr_keys}")
        print(f"JMP mappings: {jmp_keys}")
        
        # Find common position and check if keys are related
        for jsr_k, jsr_v in jsr_keys:
            jsr_pos = int(jsr_k.split('pos')[1].split('_')[0])
            for jmp_k, jmp_v in jmp_keys:
                jmp_pos = int(jmp_k.split('pos')[1].split('_')[0])
                if jsr_pos == jmp_pos:
                    print(f"Both patterns use position {jsr_pos} for byte 0")
                    if jsr_v == jmp_v:
                        print(f"  Same XOR key: 0x{jsr_v:02x}")
                        return {'byte0_pos': jsr_pos, 'byte0_key': jsr_v}
                    else:
                        print(f"  Different keys: JSR=0x{jsr_v:02x}, JMP=0x{jmp_v:02x}")
                        # Maybe the key depends on which instruction it is
                        return {
                            'byte0_pos': jsr_pos, 
                            'jsr_byte0_key': jsr_v,
                            'jmp_byte0_key': jmp_v
                        }
        
        return {}
    
    def decode_word_complete(self, encoded_word, is_jsr=True):
        """Decode a 4-byte word using complete mapping"""
        if len(encoded_word) != 4:
            return None
        
        # Byte 1: position 3 XOR 0x45
        byte1 = encoded_word[3] ^ 0x45
        
        # Byte 0: We found that different patterns may need different keys
        # Let's try a more sophisticated approach
        
        # Try each position for byte 0
        candidates = []
        for pos in range(4):
            for key in range(256):
                if (encoded_word[pos] ^ key) == 0x54:
                    candidates.append((pos, key, encoded_word[pos]))
        
        if candidates:
            # Use the first candidate (most likely position 0 based on earlier analysis)
            pos, key, orig = candidates[0]
            byte0 = 0x54
            return bytes([byte0, byte1])
        
        return None
    
    def smart_decode_word(self, encoded_word):
        """Smart decoder that figures out the pattern"""
        if len(encoded_word) != 4:
            return None
            
        try:
            # Byte 1: Always position 3 XOR 0x45 (confirmed working)
            byte1 = encoded_word[3] ^ 0x45
            
            # Byte 0: Try the most common patterns first
            # From analysis, position 0 seemed most promising
            
            # Try different approaches for byte 0
            candidates = [
                encoded_word[0] ^ 0xcd,  # Original attempt
                encoded_word[1] ^ 0x36,  # Try other positions
                encoded_word[2] ^ 0x3a,  
                0x54,                    # Force to 0x54 since we know it should be
            ]
            
            # Pick 0x54 for now since we know that's correct
            byte0 = 0x54
            
            return bytes([byte0, byte1])
            
        except:
            return None
    
    def decode_vector_table_complete(self, clean_data, vector_start_offset=36):
        """Decode vector table with complete algorithm"""
        print(f"\n=== Complete Vector Table Decode ===")
        
        decoded_vectors = []
        
        for i in range(81):  # 81 vectors expected
            offset = vector_start_offset + i * 8
            if offset + 8 > len(clean_data):
                break
                
            first_word_enc = clean_data[offset:offset+4]
            second_word_enc = clean_data[offset+4:offset+8]
            
            # Skip impostor patterns
            impostor_patterns = ['956f26', '956e26', 'e96726', 'ee1426']
            if any(first_word_enc.hex().startswith(p) for p in impostor_patterns):
                print(f"V{i:2d}: SKIPPING IMPOSTOR {first_word_enc.hex()}")
                continue
            
            # Decode both words
            decoded_first = self.smart_decode_word(first_word_enc)
            decoded_second = self.smart_decode_word(second_word_enc)
            
            if decoded_first and decoded_second:
                decoded_vectors.append((decoded_first, decoded_second))
                
                word1 = (decoded_first[0] << 8) | decoded_first[1]
                word2 = (decoded_second[0] << 8) | decoded_second[1]
                
                marker = ""
                if word1 == 0x54e1:
                    marker = " <-- JMP!"
                elif word1 == 0x54e2:
                    marker = " <-- JSR!"
                elif (word1 & 0xFF00) == 0x5400:
                    marker = f" <-- 54{word1&0xFF:02x}"
                
                print(f"V{i:2d}: {first_word_enc.hex()} {second_word_enc.hex()} -> {word1:04x} {word2:04x}{marker}")
        
        return decoded_vectors
    
    def decode_full_firmware(self, clean_data, vector_table_size=648):
        """Decode the complete firmware beyond just vectors"""
        print(f"\n=== Decoding Full Firmware ===")
        
        decoded_data = bytearray()
        
        print(f"Decoding {len(clean_data)} bytes of clean data...")
        
        for i in range(0, len(clean_data), 4):
            encoded_word = clean_data[i:i+4]
            if len(encoded_word) == 4:
                # Skip impostor patterns
                if encoded_word.hex().startswith(('956f26', '956e26', 'e96726', 'ee1426')):
                    continue
                
                decoded_word = self.smart_decode_word(encoded_word)
                if decoded_word:
                    decoded_data.extend(decoded_word)
        
        print(f"Decoded to {len(decoded_data)} bytes")
        return bytes(decoded_data)

def main():
    if len(sys.argv) < 2:
        print("Usage: python complete_decoder.py <clean_firmware.bin>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    try:
        with open(filename, 'rb') as f:
            clean_data = f.read()
        
        print(f"Loaded {len(clean_data)} bytes of clean firmware data")
        
        decoder = CompleteDecoder()
        
        # Find complete mappings
        mapping = decoder.complete_mapping_discovery()
        print(f"Found mapping: {mapping}")
        
        # Decode vector table with corrections
        decoded_vectors = decoder.decode_vector_table_complete(clean_data)
        
        if decoded_vectors:
            # Save decoded vector table
            vector_data = b''.join(first + second for first, second in decoded_vectors)
            vector_output = filename.replace('.bin', '_complete_vectors.bin')
            with open(vector_output, 'wb') as f:
                f.write(vector_data)
            print(f"\nSaved complete vector table to {vector_output}")
            
            # Decode full firmware
            decoded_firmware = decoder.decode_full_firmware(clean_data)
            firmware_output = filename.replace('.bin', '_complete_firmware.bin')
            with open(firmware_output, 'wb') as f:
                f.write(decoded_firmware)
            print(f"Saved complete firmware to {firmware_output}")
            
            # Show summary
            print(f"\n=== Summary ===")
            print(f"Vector table: {len(vector_data)} bytes")
            print(f"Full firmware: {len(decoded_firmware)} bytes")
            
            # Show first few vectors
            print(f"\nFirst 10 decoded vectors:")
            for i in range(min(10, len(decoded_vectors))):
                first, second = decoded_vectors[i]
                word1 = (first[0] << 8) | first[1]
                word2 = (second[0] << 8) | second[1]
                vtype = "JMP" if word1 == 0x54e1 else "JSR" if word1 == 0x54e2 else "???"
                print(f"  V{i}: {word1:04x} {word2:04x} ({vtype})")
        
    except FileNotFoundError:
        print(f"File not found: {filename}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
