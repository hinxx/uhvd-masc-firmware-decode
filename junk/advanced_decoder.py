#!/usr/bin/env python3
"""
Advanced Firmware Decoder - trying to crack the MC56F8345 encoding
"""

import sys

class AdvancedDecoder:
    def __init__(self):
        self.preamble_bytes = [0x80, 0xaf, 0xa9]
    
    def try_all_methods(self, data, max_bytes=16):
        """Try various decoding methods on a small sample"""
        print(f"Trying decoding methods on first {max_bytes} bytes: {data[:max_bytes].hex()}")
        print()
        
        methods = [
            ("Simple XOR 0x80", self.xor_simple, [0x80]),
            ("Simple XOR 0xa9", self.xor_simple, [0xa9]),
            ("Rotating XOR (preamble)", self.xor_rotating, self.preamble_bytes),
            ("Stateful XOR (0x80 seed)", self.xor_stateful, [0x80]),
            ("Stateful XOR (0xa9 seed)", self.xor_stateful, [0xa9]),
            ("Running sum decode", self.running_sum_decode, []),
            ("Byte pair subtract", self.byte_pair_subtract, []),
            ("Byte pair add", self.byte_pair_add, []),
            ("Position-based XOR", self.position_xor, []),
        ]
        
        for name, method, args in methods:
            try:
                result = method(data[:max_bytes], *args)
                print(f"{name:25}: {result.hex()}")
                
                # Check if this looks like valid DSP56800E opcodes
                if len(result) >= 4:
                    word1 = (result[0] << 8) | result[1]
                    word2 = (result[2] << 8) | result[3] if len(result) >= 4 else 0
                    
                    # Check for JMP (54 e1) or JSR (54 e2) patterns
                    if result[0] == 0x54 and result[1] in [0xe1, 0xe2]:
                        print(f"                         *** POSSIBLE MATCH: {word1:04x} {word2:04x} ***")
                    elif any(result[i:i+2] == bytes([0x54, 0xe1]) for i in range(len(result)-1)):
                        print(f"                         *** Contains 54 e1 pattern ***")
                    elif any(result[i:i+2] == bytes([0x54, 0xe2]) for i in range(len(result)-1)):
                        print(f"                         *** Contains 54 e2 pattern ***")
                        
            except Exception as e:
                print(f"{name:25}: ERROR - {e}")
        print()
    
    def xor_simple(self, data, key):
        return bytes(b ^ key for b in data)
    
    def xor_rotating(self, data, keys):
        return bytes(data[i] ^ keys[i % len(keys)] for i in range(len(data)))
    
    def xor_stateful(self, data, seed):
        result = bytearray()
        state = seed
        for b in data:
            decoded = b ^ state
            result.append(decoded)
            state = b  # state becomes the encoded byte
        return bytes(result)
    
    def running_sum_decode(self, data):
        """Try decoding where each byte is sum of previous bytes"""
        result = bytearray()
        running_sum = 0
        for b in data:
            decoded = (b - running_sum) & 0xFF
            result.append(decoded)
            running_sum = (running_sum + decoded) & 0xFF
        return bytes(result)
    
    def byte_pair_subtract(self, data):
        """Each pair of bytes: second - first"""
        result = bytearray()
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                result.append((data[i+1] - data[i]) & 0xFF)
        return bytes(result)
    
    def byte_pair_add(self, data):
        """Each pair of bytes: first + second"""
        result = bytearray()
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                result.append((data[i] + data[i+1]) & 0xFF)
        return bytes(result)
    
    def position_xor(self, data):
        """XOR each byte with its position"""
        return bytes(data[i] ^ (i & 0xFF) for i in range(len(data)))
    
    def test_vector_patterns(self, clean_data):
        """Test decoding on known vector patterns"""
        print("=== Testing Vector Patterns ===")
        
        # Test the first vector (should be JMP 54 e1 XXXX)
        v0 = clean_data[0:8]  # First vector is 8 bytes (2 words encoded)
        print(f"Vector 0 (8 bytes): {v0.hex()}")
        self.try_all_methods(v0)
        
        # Test the repeating JSR pattern
        jsr_pattern = bytes.fromhex('8e548b6d99626ea7')
        print(f"Common JSR pattern: {jsr_pattern.hex()}")
        self.try_all_methods(jsr_pattern)
        
        # Test another common pattern
        pattern2 = bytes.fromhex('fa558269')
        print(f"Most common 4-byte pattern: {pattern2.hex()}")
        self.try_all_methods(pattern2, 4)
    
    def decode_full_vector_table(self, clean_data, method_name="xor_stateful", *args):
        """Decode the full vector table using specified method"""
        print(f"=== Decoding Vector Table with {method_name} ===")
        
        method_map = {
            "xor_stateful": self.xor_stateful,
            "xor_rotating": self.xor_rotating,
            "xor_simple": self.xor_simple,
        }
        
        if method_name not in method_map:
            print(f"Unknown method: {method_name}")
            return
        
        method = method_map[method_name]
        
        # Decode first 81 vectors (324 bytes -> 162 words)
        vector_data = clean_data[:324]  # 81 vectors * 4 encoded bytes each
        
        try:
            decoded = method(vector_data, *args)
            print(f"Decoded {len(decoded)} bytes from {len(vector_data)} encoded bytes")
            
            print("\nVector table (first 20 entries):")
            print("Vec#  Word1  Word2  (Word1 should be 54e1 for JMP, 54e2 for JSR)")
            print("----  -----  -----")
            
            for i in range(min(20, len(decoded) // 4)):
                offset = i * 4
                if offset + 3 < len(decoded):
                    word1 = (decoded[offset] << 8) | decoded[offset + 1]
                    word2 = (decoded[offset + 2] << 8) | decoded[offset + 3]
                    
                    # Highlight JMP/JSR patterns
                    marker = ""
                    if word1 == 0x54e1:
                        marker = " <-- JMP"
                    elif word1 == 0x54e2:
                        marker = " <-- JSR"
                    elif (word1 & 0xFF00) == 0x5400:
                        marker = f" <-- 54{word1&0xFF:02x}"
                    
                    print(f"V{i:2d}:  {word1:04x}  {word2:04x}{marker}")
            
            return decoded
            
        except Exception as e:
            print(f"Error decoding: {e}")
            return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python advanced_decoder.py <clean_firmware.bin>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    try:
        with open(filename, 'rb') as f:
            clean_data = f.read()
        
        print(f"Loaded {len(clean_data)} bytes of clean firmware data")
        print()
        
        decoder = AdvancedDecoder()
        
        # Test various patterns
        decoder.test_vector_patterns(clean_data)
        
        # Try the most promising methods for full decode
        print("=" * 60)
        
        # Try stateful XOR with different seeds
        for seed in [0x80, 0xa9, 0xaf]:
            print(f"Trying stateful XOR with seed 0x{seed:02x}:")
            result = decoder.decode_full_vector_table(clean_data, "xor_stateful", seed)
            print()
        
        # Try rotating XOR with preamble
        print("Trying rotating XOR with preamble bytes:")
        result = decoder.decode_full_vector_table(clean_data, "xor_rotating", [0x80, 0xaf, 0xa9])
        
        # Save the best result
        if result:
            output_file = filename.replace('.bin', '_decoded.bin')
            with open(output_file, 'wb') as f:
                f.write(result[:324])  # Just the vector table for now
            print(f"Saved decoded vector table to {output_file}")
        
    except FileNotFoundError:
        print(f"File not found: {filename}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
