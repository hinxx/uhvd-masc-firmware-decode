#!/usr/bin/env python3
"""
Targeted Vector Decoder - Using discovered patterns to crack the encoding
"""

import sys

class TargetedDecoder:
    def __init__(self):
        # Known patterns from vector table analysis
        self.patterns = {
            'jsr_opcode': bytes.fromhex('99626ea7'),    # Should decode to 54 e2
            'jmp_opcode_1': bytes.fromhex('9560269f'),  # Should decode to 54 e1  
            'jmp_opcode_2': bytes.fromhex('99626ea4'),  # Should decode to 54 e1
            'jsr_target': bytes.fromhex('8e548b6d'),    # Default JSR target
            'jmp_targets': [
                bytes.fromhex('fa558269'),  # JMP target 1
                bytes.fromhex('8b518269'),  # JMP target 2
            ]
        }
        
        # Expected decoded values
        self.expected = {
            'jsr_opcode': bytes([0x54, 0xe2]),
            'jmp_opcode': bytes([0x54, 0xe1]),
        }
    
    def analyze_byte_differences(self):
        """Analyze byte-by-byte differences between patterns"""
        print("=== Analyzing Pattern Differences ===")
        
        jsr = self.patterns['jsr_opcode']
        jmp1 = self.patterns['jmp_opcode_1'] 
        jmp2 = self.patterns['jmp_opcode_2']
        
        print(f"JSR:  {jsr.hex()}")
        print(f"JMP1: {jmp1.hex()}")
        print(f"JMP2: {jmp2.hex()}")
        print()
        
        print("Byte differences:")
        for i in range(4):
            print(f"  Byte {i}: JSR=0x{jsr[i]:02x}, JMP1=0x{jmp1[i]:02x}, JMP2=0x{jmp2[i]:02x}")
            print(f"           JSR^JMP1=0x{jsr[i]^jmp1[i]:02x}, JSR^JMP2=0x{jsr[i]^jmp2[i]:02x}")
        print()
        
        # The difference between JSR (54 e2) and JMP (54 e1) should be in the second byte
        # So we need to find which encoded byte position corresponds to the second decoded byte
    
    def brute_force_position_mapping(self):
        """Try to find which encoded bytes map to which decoded bytes"""
        print("=== Brute Force Position Mapping ===")
        
        jsr_enc = self.patterns['jsr_opcode']
        jmp_enc = self.patterns['jmp_opcode_2']  # 99626ea4 vs 99626ea7 (differs in last byte)
        
        jsr_exp = self.expected['jsr_opcode']  # 54 e2
        jmp_exp = self.expected['jmp_opcode']  # 54 e1
        
        print(f"JSR encoded: {jsr_enc.hex()} -> expected: {jsr_exp.hex()}")
        print(f"JMP encoded: {jmp_enc.hex()} -> expected: {jmp_exp.hex()}")
        print()
        
        # The only difference is byte 3: 0xa7 vs 0xa4
        # And the expected difference is byte 1: 0xe2 vs 0xe1
        # So encoded byte 3 might map to decoded byte 1
        
        print("Key insight: JSR vs JMP differs in:")
        print(f"  Encoded byte 3: 0x{jsr_enc[3]:02x} vs 0x{jmp_enc[3]:02x} (diff: 0x{jsr_enc[3]^jmp_enc[3]:02x})")
        print(f"  Expected byte 1: 0x{jsr_exp[1]:02x} vs 0x{jmp_exp[1]:02x} (diff: 0x{jsr_exp[1]^jmp_exp[1]:02x})")
        
        # Let's see if there's a simple relationship
        # 0xa7 should map to 0xe2, 0xa4 should map to 0xe1
        
        if jsr_enc[3] ^ jmp_enc[3] == jsr_exp[1] ^ jmp_exp[1]:
            print("  XOR differences match! Encoded byte 3 might XOR-decode to decoded byte 1")
            
            # Find the XOR key
            key = jsr_enc[3] ^ jsr_exp[1]
            print(f"  XOR key for byte 3->1: 0x{key:02x}")
            
            # Test this key
            test_jsr = jsr_enc[3] ^ key
            test_jmp = jmp_enc[3] ^ key
            print(f"  Test: 0x{jsr_enc[3]:02x} ^ 0x{key:02x} = 0x{test_jsr:02x} (expected 0x{jsr_exp[1]:02x})")
            print(f"  Test: 0x{jmp_enc[3]:02x} ^ 0x{key:02x} = 0x{test_jmp:02x} (expected 0x{jmp_exp[1]:02x})")
            
            if test_jsr == jsr_exp[1] and test_jmp == jmp_exp[1]:
                print("  SUCCESS! Found mapping for position 3->1")
                return {'pos3_to_pos1_key': key}
        
        # Try other position mappings
        print("\nTrying other position mappings...")
        
        for enc_pos in range(4):
            for dec_pos in range(2):
                for key in range(256):
                    jsr_test = (jsr_enc[enc_pos] ^ key) & 0xFF
                    jmp_test = (jmp_enc[enc_pos] ^ key) & 0xFF
                    
                    if jsr_test == jsr_exp[dec_pos] and jmp_test == jmp_exp[dec_pos]:
                        print(f"  Found mapping: encoded pos {enc_pos} XOR 0x{key:02x} -> decoded pos {dec_pos}")
                        return {f'pos{enc_pos}_to_pos{dec_pos}_key': key}
        
        print("No simple XOR mapping found")
        return {}
    
    def try_comprehensive_decode(self, mapping_info):
        """Try to decode using discovered mapping"""
        if not mapping_info:
            print("No mapping info available")
            return
            
        print(f"\n=== Trying Comprehensive Decode ===")
        
        # Use the discovered mapping to try decoding all patterns
        patterns_to_test = [
            ('JSR opcode', self.patterns['jsr_opcode'], self.expected['jsr_opcode']),
            ('JMP opcode 1', self.patterns['jmp_opcode_1'], self.expected['jmp_opcode']),
            ('JMP opcode 2', self.patterns['jmp_opcode_2'], self.expected['jmp_opcode']),
        ]
        
        for name, encoded, expected in patterns_to_test:
            print(f"\n{name}: {encoded.hex()} -> {expected.hex()}")
            
            # Try to decode both bytes
            decoded = [0, 0]
            
            # If we found a mapping for position 3->1, use it
            if 'pos3_to_pos1_key' in mapping_info:
                key = mapping_info['pos3_to_pos1_key']
                decoded[1] = encoded[3] ^ key
                print(f"  Byte 1: encoded[3] ^ 0x{key:02x} = 0x{decoded[1]:02x}")
                
                # Now try to find mapping for byte 0
                # Byte 0 should always be 0x54
                for pos in range(4):
                    for k in range(256):
                        if (encoded[pos] ^ k) == 0x54:
                            decoded[0] = encoded[pos] ^ k  
                            print(f"  Byte 0: encoded[{pos}] ^ 0x{k:02x} = 0x{decoded[0]:02x}")
                            break
                    if decoded[0] == 0x54:
                        mapping_info[f'pos{pos}_to_pos0_key'] = k
                        break
                
                result = bytes(decoded)
                print(f"  Decoded: {result.hex()}")
                
                if result == expected:
                    print(f"  SUCCESS! {name} decoded correctly")
                else:
                    print(f"  FAILED: expected {expected.hex()}")
    
    def decode_vector_table(self, clean_data, vector_start_offset=36):
        """Decode the entire vector table using discovered patterns"""
        print(f"\n=== Decoding Vector Table ===")
        
        # First find the mappings
        mapping = self.brute_force_position_mapping()
        if not mapping:
            print("Could not find position mappings")
            return None
            
        print(f"Using mappings: {mapping}")
        
        decoded_vectors = []
        
        for i in range(81):  # 81 vectors
            offset = vector_start_offset + i * 8
            if offset + 8 > len(clean_data):
                break
                
            first_word_enc = clean_data[offset:offset+4]
            second_word_enc = clean_data[offset+4:offset+8]
            
            # Skip impostor patterns
            if any(first_word_enc.startswith(bytes.fromhex(p)) for p in ['956f26', '956e26', 'e96726', 'ee1426']):
                print(f"V{i:2d}: SKIPPING IMPOSTOR {first_word_enc.hex()}")
                continue
            
            # Decode first word (opcode)
            decoded_first = self.decode_word(first_word_enc, mapping)
            decoded_second = self.decode_word(second_word_enc, mapping)
            
            if decoded_first and decoded_second:
                decoded_vectors.append((decoded_first, decoded_second))
                
                word1 = (decoded_first[0] << 8) | decoded_first[1]
                word2 = (decoded_second[0] << 8) | decoded_second[1]
                
                marker = ""
                if decoded_first[0] == 0x54:
                    if decoded_first[1] == 0xe1:
                        marker = " <-- JMP!"
                    elif decoded_first[1] == 0xe2:
                        marker = " <-- JSR!"
                
                print(f"V{i:2d}: {first_word_enc.hex()} {second_word_enc.hex()} -> {word1:04x} {word2:04x}{marker}")
        
        return decoded_vectors
    
    def decode_word(self, encoded_word, mapping):
        """Decode a single 4-byte encoded word to 2 bytes"""
        if len(encoded_word) != 4:
            return None
            
        try:
            decoded = [0, 0]
            
            # Apply discovered mappings
            if 'pos3_to_pos1_key' in mapping:
                decoded[1] = encoded_word[3] ^ mapping['pos3_to_pos1_key']
            
            if 'pos0_to_pos0_key' in mapping:
                decoded[0] = encoded_word[0] ^ mapping['pos0_to_pos0_key']
            elif 'pos1_to_pos0_key' in mapping:
                decoded[0] = encoded_word[1] ^ mapping['pos1_to_pos0_key']  
            elif 'pos2_to_pos0_key' in mapping:
                decoded[0] = encoded_word[2] ^ mapping['pos2_to_pos0_key']
            
            return bytes(decoded)
            
        except:
            return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python targeted_decoder.py <clean_firmware.bin>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    try:
        with open(filename, 'rb') as f:
            clean_data = f.read()
        
        print(f"Loaded {len(clean_data)} bytes of clean firmware data")
        
        decoder = TargetedDecoder()
        
        # Analyze the patterns
        decoder.analyze_byte_differences()
        
        # Find position mappings
        mapping = decoder.brute_force_position_mapping()
        
        # Try comprehensive decode
        decoder.try_comprehensive_decode(mapping)
        
        # Decode the vector table
        if mapping:
            decoded_vectors = decoder.decode_vector_table(clean_data)
            
            if decoded_vectors:
                # Save decoded vector table
                output_data = b''.join(first + second for first, second in decoded_vectors)
                output_file = filename.replace('.bin', '_decoded_vectors.bin')
                with open(output_file, 'wb') as f:
                    f.write(output_data)
                print(f"\nSaved decoded vector table to {output_file}")
                
                # Show hex dump
                print(f"\nDecoded vector table hex dump:")
                for i in range(0, min(80, len(output_data)), 16):
                    hex_part = " ".join(f"{output_data[i+j]:02x}" for j in range(min(16, len(output_data)-i)))
                    addr = f"{i:04x}"
                    print(f"{addr}: {hex_part}")
        
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
