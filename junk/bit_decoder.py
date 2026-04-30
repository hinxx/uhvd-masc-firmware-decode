#!/usr/bin/env python3
"""
Bit-level analysis decoder for MC56F8345 firmware
"""

import sys
from collections import Counter

class BitLevelDecoder:
    def __init__(self):
        self.preamble_bytes = [0x80, 0xaf, 0xa9]
    
    def analyze_bit_patterns(self, data, num_bytes=32):
        """Analyze bit patterns in the data"""
        print(f"=== Bit Pattern Analysis ===")
        print(f"Analyzing first {num_bytes} bytes: {data[:num_bytes].hex()}")
        print()
        
        # Look at bit distribution
        bit_positions = [0] * 8
        for byte in data[:num_bytes]:
            for i in range(8):
                if byte & (1 << i):
                    bit_positions[i] += 1
        
        print("Bit position frequency (bit 0 = LSB):")
        for i in range(8):
            print(f"  Bit {i}: {bit_positions[i]}/{num_bytes} = {bit_positions[i]/num_bytes:.2%}")
        print()
        
        # Look for patterns that might indicate Manchester encoding or similar
        print("Byte transitions (might show encoding structure):")
        for i in range(min(16, num_bytes-1)):
            b1, b2 = data[i], data[i+1]
            xor = b1 ^ b2
            print(f"  {i:2d}: {b1:02x} -> {b2:02x} (XOR: {xor:02x} = {bin(xor)[2:].zfill(8)})")
    
    def try_bit_extraction(self, data, max_bytes=16):
        """Try various bit extraction methods"""
        print(f"=== Bit Extraction Methods ===")
        print(f"Input: {data[:max_bytes].hex()}")
        print()
        
        methods = [
            ("Every 2nd bit", self.extract_every_nth_bit, [2]),
            ("Every 3rd bit", self.extract_every_nth_bit, [3]),
            ("Every 4th bit", self.extract_every_nth_bit, [4]),
            ("Even bits only", self.extract_even_bits, []),
            ("Odd bits only", self.extract_odd_bits, []),
            ("High nibble decode", self.decode_high_nibbles, []),
            ("Low nibble decode", self.decode_low_nibbles, []),
            ("Interleaved decode", self.decode_interleaved, []),
        ]
        
        for name, method, args in methods:
            try:
                result = method(data[:max_bytes], *args)
                print(f"{name:20}: {result.hex()}")
                
                # Check for JMP/JSR patterns
                if len(result) >= 2:
                    if result[0] == 0x54 and result[1] in [0xe1, 0xe2]:
                        print(f"                     *** FOUND {result[0]:02x} {result[1]:02x} PATTERN! ***")
                        
            except Exception as e:
                print(f"{name:20}: ERROR - {e}")
        print()
    
    def extract_every_nth_bit(self, data, n):
        """Extract every nth bit from the data"""
        all_bits = []
        for byte in data:
            for i in range(8):
                all_bits.append((byte >> i) & 1)
        
        # Take every nth bit
        selected_bits = all_bits[::n]
        
        # Pack back into bytes
        result = bytearray()
        for i in range(0, len(selected_bits), 8):
            byte = 0
            for j in range(8):
                if i + j < len(selected_bits):
                    byte |= selected_bits[i + j] << j
            result.append(byte)
        
        return bytes(result)
    
    def extract_even_bits(self, data):
        """Extract bits at even positions"""
        result = bytearray()
        for byte in data:
            new_byte = 0
            for i in range(0, 8, 2):
                if byte & (1 << i):
                    new_byte |= (1 << (i // 2))
            result.append(new_byte)
        return bytes(result)
    
    def extract_odd_bits(self, data):
        """Extract bits at odd positions"""
        result = bytearray()
        for byte in data:
            new_byte = 0
            for i in range(1, 8, 2):
                if byte & (1 << i):
                    new_byte |= (1 << (i // 2))
            result.append(new_byte)
        return bytes(result)
    
    def decode_high_nibbles(self, data):
        """Extract only high nibbles"""
        result = bytearray()
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                hi1 = (data[i] >> 4) & 0xF
                hi2 = (data[i+1] >> 4) & 0xF
                result.append((hi1 << 4) | hi2)
        return bytes(result)
    
    def decode_low_nibbles(self, data):
        """Extract only low nibbles"""
        result = bytearray()
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                lo1 = data[i] & 0xF
                lo2 = data[i+1] & 0xF
                result.append((lo1 << 4) | lo2)
        return bytes(result)
    
    def decode_interleaved(self, data):
        """Try interleaved high/low nibbles"""
        result = bytearray()
        for i in range(0, len(data), 4):
            if i + 3 < len(data):
                # Take high nibble from bytes 0,2 and low from bytes 1,3
                b0 = ((data[i] >> 4) & 0xF) << 4 | (data[i+1] & 0xF)
                b1 = ((data[i+2] >> 4) & 0xF) << 4 | (data[i+3] & 0xF)
                result.append(b0)
                result.append(b1)
        return bytes(result)
    
    def try_known_vector_decode(self, clean_data):
        """Try to decode known vector patterns"""
        print("=== Known Vector Analysis ===")
        
        # We know the first vector should be JMP 54 e1 XXXX
        # Let's see if we can work backwards from this
        
        v0_encoded = clean_data[0:4]  # First word
        v1_encoded = clean_data[4:8]  # Second word
        
        print(f"V0 first word (encoded): {v0_encoded.hex()}")
        print(f"V0 second word (encoded): {v1_encoded.hex()}")
        print()
        
        # If V0 first word should decode to 0x54e1, try to find the transformation
        target = bytes([0x54, 0xe1])
        
        print("Trying to find transformation that maps encoded -> 0x54e1:")
        
        # Try each possible XOR key
        for key in range(256):
            test = bytes(b ^ key for b in v0_encoded)
            if test.startswith(target):
                print(f"  XOR with 0x{key:02x}: {test.hex()} - MATCH!")
        
        # Try each possible rotating XOR with 2-byte key
        for key1 in range(0, 256, 16):  # Reduce search space
            for key2 in range(0, 256, 16):
                test = bytes(v0_encoded[i] ^ [key1, key2][i % 2] for i in range(len(v0_encoded)))
                if test.startswith(target):
                    print(f"  Rotating XOR [{key1:02x},{key2:02x}]: {test.hex()} - MATCH!")
        
        # Try addition/subtraction
        for delta in range(256):
            test_add = bytes((b + delta) & 0xFF for b in v0_encoded)
            test_sub = bytes((b - delta) & 0xFF for b in v0_encoded)
            if test_add.startswith(target):
                print(f"  Add 0x{delta:02x}: {test_add.hex()} - MATCH!")
            if test_sub.startswith(target):
                print(f"  Sub 0x{delta:02x}: {test_sub.hex()} - MATCH!")
        
        print("No simple transformation found for first vector")
        print()
    
    def analyze_nibble_constraints(self, clean_data):
        """Analyze the nibble constraints we found earlier"""
        print("=== Nibble Constraint Analysis ===")
        
        # Re-analyze the position-dependent nibble constraints
        hi_nibbles_by_pos = [Counter() for _ in range(4)]
        lo_nibbles_by_pos = [Counter() for _ in range(4)]
        
        for i in range(0, min(1000, len(clean_data)), 4):
            for pos in range(4):
                if i + pos < len(clean_data):
                    byte = clean_data[i + pos]
                    hi_nibbles_by_pos[pos][(byte >> 4) & 0xF] += 1
                    lo_nibbles_by_pos[pos][byte & 0xF] += 1
        
        print("High nibble constraints by position:")
        for pos in range(4):
            allowed_hi = sorted(hi_nibbles_by_pos[pos].keys())
            print(f"  Position {pos}: {[f'{n:x}' for n in allowed_hi]} ({len(allowed_hi)} values)")
        
        print("\nLow nibble usage by position:")
        for pos in range(4):
            lo_count = len(lo_nibbles_by_pos[pos])
            print(f"  Position {pos}: {lo_count} distinct values")
        
        # The fact that high nibbles are constrained suggests they encode the data
        # Let's try to map the 4 allowed values to 2 bits each
        print("\nTrying to decode high nibbles as 2-bit values:")
        
        # For position 0, we found: 8,9,E,F mostly
        # Let's map these to 00,01,10,11
        pos0_map = {0x8: 0, 0x9: 1, 0xE: 2, 0xF: 3}
        pos1_map = {0x1: 0, 0x2: 1, 0x5: 2, 0x6: 3}
        
        print("Position 0 map (8->0, 9->1, E->2, F->3):")
        print("Position 1 map (1->0, 2->1, 5->2, 6->3):")
        
        # Test on first few 4-byte groups
        for i in range(min(5, len(clean_data) // 4)):
            group = clean_data[i*4:(i+1)*4]
            print(f"Group {i}: {group.hex()}")
            
            try:
                # Extract high nibbles and map to 2-bit values
                hi0 = (group[0] >> 4) & 0xF
                hi1 = (group[1] >> 4) & 0xF
                
                if hi0 in pos0_map and hi1 in pos1_map:
                    val0 = pos0_map[hi0]
                    val1 = pos1_map[hi1]
                    
                    # Combine into a byte (4 bits total from first 2 positions)
                    decoded_nibble = (val0 << 2) | val1
                    print(f"  Hi nibbles {hi0:x},{hi1:x} -> {val0},{val1} -> {decoded_nibble:04b} = 0x{decoded_nibble:x}")
                else:
                    print(f"  Hi nibbles {hi0:x},{hi1:x} - not in expected sets")
                    
            except:
                pass
        
        print()

def main():
    if len(sys.argv) < 2:
        print("Usage: python bit_decoder.py <clean_firmware.bin>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    try:
        with open(filename, 'rb') as f:
            clean_data = f.read()
        
        print(f"Loaded {len(clean_data)} bytes of clean firmware data")
        print()
        
        decoder = BitLevelDecoder()
        
        # Bit pattern analysis
        decoder.analyze_bit_patterns(clean_data)
        
        # Try bit extraction methods
        decoder.try_bit_extraction(clean_data)
        
        # Try to work backwards from known vector format
        decoder.try_known_vector_decode(clean_data)
        
        # Analyze nibble constraints
        decoder.analyze_nibble_constraints(clean_data)
        
    except FileNotFoundError:
        print(f"File not found: {filename}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
