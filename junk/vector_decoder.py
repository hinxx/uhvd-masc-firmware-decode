#!/usr/bin/env python3
"""
Vector Table Decoder for MC56F8345 firmware
Based on pattern analysis to find actual vector table location
"""

import sys
from collections import Counter

class VectorTableDecoder:
    def __init__(self):
        # Known patterns from analysis
        self.jsr_pattern = bytes.fromhex('99626ea7')      # Common JSR pattern
        self.jsr_alt_pattern = bytes.fromhex('99626ea4')  # Alternate JSR (might be JMP)
        self.impostor_pattern = bytes.fromhex('956f269f') # Impostor bytes
        
    def find_vector_table_start(self, clean_data):
        """Find where the actual vector table starts by looking for JSR patterns"""
        print("=== Finding Vector Table Start ===")
        
        # Look for the first occurrence of the JSR pattern
        jsr_positions = []
        jmp_positions = []  # For the alternate pattern
        
        for i in range(0, len(clean_data) - 4, 4):  # Check every 4-byte boundary
            pattern = clean_data[i:i+4]
            if pattern == self.jsr_pattern:
                jsr_positions.append(i)
            elif pattern == self.jsr_alt_pattern:
                jmp_positions.append(i)
        
        print(f"Found JSR pattern {self.jsr_pattern.hex()} at positions: {jsr_positions[:20]}...")
        print(f"Found JMP pattern {self.jsr_alt_pattern.hex()} at positions: {jmp_positions[:20]}...")
        
        # The vector table should start with 2 JMP entries followed by JSR entries
        # Look for a position where we have JMP, JMP, then many JSR patterns
        
        candidates = []
        
        # Check each JMP position to see if it's followed by another JMP then JSRs
        for jmp_pos in jmp_positions:
            if jmp_pos >= 8:  # Need space for previous entries
                # Check if there's another JMP 8 bytes earlier (previous vector)
                prev_pos = jmp_pos - 8
                if prev_pos >= 0:
                    prev_pattern = clean_data[prev_pos:prev_pos+4]
                    if prev_pattern == self.jsr_alt_pattern:  # Another JMP
                        # This could be vector 1, so vector 0 would be at prev_pos - 8
                        vector_start = prev_pos - 8
                        if vector_start >= 0:
                            print(f"Potential vector table start at offset {vector_start} (0x{vector_start:x})")
                            candidates.append(vector_start)
        
        # Also check if JSR patterns start after some JMP patterns
        if jsr_positions and jmp_positions:
            min_jsr = min(jsr_positions)
            nearby_jmps = [pos for pos in jmp_positions if pos < min_jsr and pos >= min_jsr - 16]
            if nearby_jmps:
                # Vector table might start before the JMPs
                for jmp_pos in nearby_jmps:
                    vector_start = jmp_pos - 8  # Assume this is vector 1
                    if vector_start >= 0:
                        print(f"Potential vector table start at offset {vector_start} (0x{vector_start:x}) based on JSR proximity")
                        candidates.append(vector_start)
        
        return candidates
    
    def analyze_vector_sequence(self, clean_data, start_offset):
        """Analyze the vector sequence starting at a given offset"""
        print(f"\n=== Analyzing Vector Sequence at Offset {start_offset} (0x{start_offset:x}) ===")
        
        vectors = []
        impostor_count = 0
        
        for i in range(81):  # 81 vectors expected
            offset = start_offset + i * 8  # 8 bytes per vector
            if offset + 8 > len(clean_data):
                break
                
            # Each vector is 8 bytes (2 words encoded)
            first_word = clean_data[offset:offset+4]
            second_word = clean_data[offset+4:offset+8]
            
            vectors.append((first_word, second_word))
            
            # Check for impostor patterns
            if first_word == self.impostor_pattern or second_word == self.impostor_pattern:
                impostor_count += 1
                print(f"V{i:2d}: {first_word.hex()} {second_word.hex()} <-- IMPOSTOR")
            elif first_word == self.jsr_alt_pattern:
                print(f"V{i:2d}: {first_word.hex()} {second_word.hex()} <-- JMP?")
            elif first_word == self.jsr_pattern:
                print(f"V{i:2d}: {first_word.hex()} {second_word.hex()} <-- JSR")
            else:
                print(f"V{i:2d}: {first_word.hex()} {second_word.hex()}")
            
            # Show first 10 vectors in detail
            if i < 10:
                pass  # Already printed above
        
        print(f"\nFound {impostor_count} impostor patterns in 81 vectors")
        return vectors
    
    def create_clean_vector_table(self, vectors):
        """Remove impostor patterns and create clean vector table"""
        print("\n=== Creating Clean Vector Table ===")
        
        clean_vectors = []
        removed_count = 0
        
        for i, (first_word, second_word) in enumerate(vectors):
            # Skip vectors with impostor patterns
            if (first_word == self.impostor_pattern or 
                second_word == self.impostor_pattern):
                print(f"Removing impostor at vector {i}")
                removed_count += 1
                continue
            
            clean_vectors.append((first_word, second_word))
        
        print(f"Removed {removed_count} impostor patterns")
        print(f"Clean vector table has {len(clean_vectors)} entries")
        
        return clean_vectors
    
    def analyze_patterns(self, clean_vectors):
        """Analyze the patterns in the clean vector table"""
        print("\n=== Pattern Analysis ===")
        
        first_word_patterns = Counter()
        second_word_patterns = Counter()
        
        for first_word, second_word in clean_vectors:
            first_word_patterns[first_word] += 1
            second_word_patterns[second_word] += 1
        
        print("Most common first word patterns (opcodes):")
        for pattern, count in first_word_patterns.most_common(10):
            print(f"  {pattern.hex()}: {count} times")
        
        print("\nMost common second word patterns (addresses):")
        for pattern, count in second_word_patterns.most_common(10):
            print(f"  {pattern.hex()}: {count} times")
        
        # The first 2 should be JMP (different addresses)
        # The remaining should mostly be JSR to the same default handler
        
        print(f"\nFirst 2 vectors (should be JMP):")
        for i in range(min(2, len(clean_vectors))):
            first, second = clean_vectors[i]
            print(f"  V{i}: {first.hex()} {second.hex()}")
        
        print(f"\nNext 10 vectors (should be JSR, mostly to same address):")
        for i in range(2, min(12, len(clean_vectors))):
            first, second = clean_vectors[i]
            print(f"  V{i}: {first.hex()} {second.hex()}")
    
    def attempt_decode_by_pattern_matching(self, clean_vectors):
        """Try to decode by matching against expected patterns"""
        print("\n=== Attempting Pattern-Based Decode ===")
        
        # We know:
        # - First 2 vectors: JMP 54 e1 XXXX
        # - Remaining vectors: JSR 54 e2 YYYY (mostly same YYYY)
        
        # Find the most common patterns
        first_word_counter = Counter(first for first, second in clean_vectors)
        second_word_counter = Counter(second for first, second in clean_vectors)
        
        # The JSR opcode pattern should be most common (79 out of 81 vectors)
        jsr_opcode_encoded = first_word_counter.most_common(1)[0][0]
        jsr_target_encoded = second_word_counter.most_common(1)[0][0]
        
        print(f"Most common first word (JSR opcode): {jsr_opcode_encoded.hex()}")
        print(f"Most common second word (JSR target): {jsr_target_encoded.hex()}")
        
        # The JMP opcodes should be the first 2 vectors that DON'T match the JSR pattern
        jmp_opcodes = []
        jmp_targets = []
        
        for i, (first, second) in enumerate(clean_vectors[:5]):  # Check first few
            if first != jsr_opcode_encoded:
                jmp_opcodes.append(first)
                jmp_targets.append(second)
                print(f"Vector {i} has different opcode: {first.hex()} (JMP candidate)")
        
        print(f"\nJMP opcode candidates: {[p.hex() for p in jmp_opcodes]}")
        print(f"JMP target candidates: {[p.hex() for p in jmp_targets]}")
        
        # Now try to find the decoding by assuming:
        # jsr_opcode_encoded decodes to "54 e2"
        # jmp_opcodes[0] decodes to "54 e1"
        
        if len(jmp_opcodes) > 0:
            print(f"\nTrying to decode:")
            print(f"  {jsr_opcode_encoded.hex()} -> 54 e2 (JSR)")
            print(f"  {jmp_opcodes[0].hex()} -> 54 e1 (JMP)")
            
            # Try different decoding methods
            self.try_decode_opcodes(jsr_opcode_encoded, bytes([0x54, 0xe2]))
            self.try_decode_opcodes(jmp_opcodes[0], bytes([0x54, 0xe1]))
    
    def try_decode_opcodes(self, encoded, expected):
        """Try to find the decoding method for opcodes"""
        print(f"\nTrying to decode {encoded.hex()} -> {expected.hex()}")
        
        # Method 1: Simple XOR
        for key in range(256):
            test = bytes(b ^ key for b in encoded)
            if test[:2] == expected:
                print(f"  XOR with 0x{key:02x}: {test.hex()} - MATCH!")
        
        # Method 2: Byte position operations
        # Try extracting specific bits/nibbles
        
        # Method 3: Arithmetic operations
        for delta in range(-128, 128):
            test = bytes((b + delta) & 0xFF for b in encoded)
            if test[:2] == expected:
                print(f"  Add {delta}: {test.hex()} - MATCH!")
        
        # Method 4: Nibble operations
        # High nibbles only
        hi_nibbles = [(b >> 4) & 0xF for b in encoded]
        if len(hi_nibbles) >= 4:
            # Try various combinations
            test_byte1 = (hi_nibbles[0] << 4) | hi_nibbles[1]
            test_byte2 = (hi_nibbles[2] << 4) | hi_nibbles[3]
            test = bytes([test_byte1, test_byte2])
            if test == expected:
                print(f"  Hi nibbles combined: {test.hex()} - MATCH!")
        
        print("  No simple decode found")

def main():
    if len(sys.argv) < 2:
        print("Usage: python vector_decoder.py <clean_firmware.bin>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    try:
        with open(filename, 'rb') as f:
            clean_data = f.read()
        
        print(f"Loaded {len(clean_data)} bytes of clean firmware data")
        
        decoder = VectorTableDecoder()
        
        # Find potential vector table starts
        candidates = decoder.find_vector_table_start(clean_data)
        
        if not candidates:
            print("No clear vector table start found, trying some common offsets...")
            candidates = [0, 8, 16, 24, 32, 40]  # Try various offsets
        
        # Analyze each candidate
        best_candidate = None
        best_score = -1
        
        for candidate in candidates[:3]:  # Check top 3 candidates
            print(f"\n{'='*60}")
            vectors = decoder.analyze_vector_sequence(clean_data, candidate)
            
            if len(vectors) >= 10:  # Need at least 10 vectors
                clean_vectors = decoder.create_clean_vector_table(vectors)
                
                # Score based on how many vectors we get and pattern consistency
                score = len(clean_vectors)
                if score > best_score:
                    best_score = score
                    best_candidate = (candidate, clean_vectors)
        
        if best_candidate:
            offset, clean_vectors = best_candidate
            print(f"\n{'='*60}")
            print(f"BEST CANDIDATE: Vector table at offset {offset}")
            
            # Detailed analysis of the best candidate
            decoder.analyze_patterns(clean_vectors)
            decoder.attempt_decode_by_pattern_matching(clean_vectors)
            
            # Save the clean vector table
            output_data = b''.join(first + second for first, second in clean_vectors)
            output_file = filename.replace('.bin', '_vectors_only.bin')
            with open(output_file, 'wb') as f:
                f.write(output_data)
            print(f"\nSaved clean vector table to {output_file}")
        
        else:
            print("Could not find a clear vector table pattern")
        
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
