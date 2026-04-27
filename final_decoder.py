#!/usr/bin/env python3
"""
Final decoder attempt for MC56F8345 firmware
Based on discovered nibble encoding patterns
"""

import sys

class FinalDecoder:
    def __init__(self):
        # Based on analysis, high nibbles at each position encode 2-4 bits of data
        # Position 0: {8,9,E,F} = 4 values -> 2 bits
        # Position 1: {1,2,5,6} = 4 values -> 2 bits  
        # Position 2: {1,2,6,8,F} = ~4 values -> 2 bits
        # Position 3: {1,6,9,A,D} = ~4 values -> 2 bits
        
        # Low nibbles seem to have more entropy - might be obfuscation or carry remainder data
        pass
    
    def decode_4byte_to_2byte(self, four_bytes):
        """
        Decode 4 encoded bytes to 2 decoded bytes
        Theory: each encoded byte contributes some bits to the decoded word
        """
        if len(four_bytes) != 4:
            return bytes([0xFF, 0xFF])  # Error marker
        
        # Method 1: High nibbles encode the data directly
        # Extract high nibbles and try to map them
        hi_nibbles = [(b >> 4) & 0xF for b in four_bytes]
        lo_nibbles = [b & 0xF for b in four_bytes]
        
        # Try different combinations
        methods = []
        
        # Method A: High nibbles are position-encoded 2-bit values
        pos_maps = [
            {0x8: 0, 0x9: 1, 0xE: 2, 0xF: 3},  # Position 0
            {0x1: 0, 0x2: 1, 0x5: 2, 0x6: 3},  # Position 1
            {0x1: 0, 0x6: 1, 0x8: 2, 0xF: 3},  # Position 2 (primary)
            {0x1: 0, 0x6: 1, 0xA: 2, 0xD: 3}   # Position 3 (primary)
        ]
        
        try:
            decoded_bits = 0
            for pos in range(4):
                if hi_nibbles[pos] in pos_maps[pos]:
                    bit_val = pos_maps[pos][hi_nibbles[pos]]
                    decoded_bits |= (bit_val << (6 - pos * 2))  # Pack 2 bits per position
                else:
                    # Try backup mapping for positions 2,3 that have more values
                    if pos == 2 and hi_nibbles[pos] == 0x2:
                        bit_val = 0  # Fallback
                        decoded_bits |= (bit_val << (6 - pos * 2))
                    elif pos == 3 and hi_nibbles[pos] == 0x9:
                        bit_val = 0  # Fallback  
                        decoded_bits |= (bit_val << (6 - pos * 2))
                    else:
                        return bytes([0xFF, 0xFF])  # Unknown mapping
            
            # decoded_bits now contains 8 bits from high nibbles
            # Need to get remaining 8 bits from low nibbles or somewhere else
            
            # Try: low nibbles contribute additional bits
            lo_bits = 0
            for pos in range(4):
                lo_bits |= (lo_nibbles[pos] << (pos * 4))  # Would be 16 bits total - too many
            
            # Instead: alternate approach - each encoded byte contributes 4 bits
            decoded_word = 0
            for pos in range(4):
                # Use some combination of hi and lo nibble
                # Try: XOR of hi and lo nibble gives 4 data bits
                data_nibble = hi_nibbles[pos] ^ lo_nibbles[pos]
                decoded_word |= (data_nibble << (12 - pos * 4))
            
            methods.append(("hi_xor_lo", decoded_word))
            
        except:
            pass
        
        # Method B: Low nibbles carry the data, high nibbles are position markers
        try:
            decoded_word = 0
            for pos in range(4):
                decoded_word |= (lo_nibbles[pos] << (12 - pos * 4))
            methods.append(("lo_nibbles", decoded_word))
        except:
            pass
        
        # Method C: Alternating bytes
        try:
            # Bytes 0,2 -> first decoded byte, bytes 1,3 -> second decoded byte
            byte1 = ((hi_nibbles[0] << 4) | lo_nibbles[0] + 
                    (hi_nibbles[2] << 4) | lo_nibbles[2]) & 0xFF
            byte2 = ((hi_nibbles[1] << 4) | lo_nibbles[1] + 
                    (hi_nibbles[3] << 4) | lo_nibbles[3]) & 0xFF
            decoded_word = (byte1 << 8) | byte2
            methods.append(("alternating", decoded_word))
        except:
            pass
        
        # Return the first method's result for now
        if methods:
            return bytes([(methods[0][1] >> 8) & 0xFF, methods[0][1] & 0xFF])
        else:
            return bytes([0xFF, 0xFF])
    
    def decode_firmware(self, clean_data):
        """Decode the full clean firmware data"""
        decoded_data = bytearray()
        
        print(f"Decoding {len(clean_data)} bytes...")
        
        for i in range(0, len(clean_data), 4):
            four_bytes = clean_data[i:i+4]
            if len(four_bytes) == 4:
                decoded_word = self.decode_4byte_to_2byte(four_bytes)
                decoded_data.extend(decoded_word)
            else:
                # Partial final group
                break
        
        print(f"Decoded to {len(decoded_data)} bytes")
        return bytes(decoded_data)
    
    def test_vector_decode(self, clean_data):
        """Test decoding on the vector table"""
        print("=== Testing Vector Table Decode ===")
        
        # Test first 20 vectors
        for i in range(min(20, len(clean_data) // 4)):
            four_bytes = clean_data[i*4:(i+1)*4]
            decoded = self.decode_4byte_to_2byte(four_bytes)
            
            # Display
            word = (decoded[0] << 8) | decoded[1]
            
            marker = ""
            if decoded[0] == 0x54:
                if decoded[1] == 0xe1:
                    marker = " <-- JMP!"
                elif decoded[1] == 0xe2:
                    marker = " <-- JSR!"
                else:
                    marker = f" <-- 54{decoded[1]:02x}"
            
            print(f"V{i:2d}: {four_bytes.hex()} -> {decoded.hex()} = {word:04x}{marker}")
        
        print()
    
    def analyze_xor_patterns(self, clean_data):
        """Try XOR-based decoding with discovered patterns"""
        print("=== XOR Pattern Analysis ===")
        
        # From the bit transition analysis, I noticed many XORs had lots of 1-bits
        # This suggests a possible whitening/scrambling pattern
        
        # Try XOR with the most common transition patterns
        test_patterns = [0xcc, 0xd6, 0xeb, 0xf4, 0xfa]  # From transition analysis
        
        v0 = clean_data[0:4]  # First vector should be JMP
        print(f"Testing XOR patterns on V0: {v0.hex()}")
        
        for pattern in test_patterns:
            decoded = bytes(b ^ pattern for b in v0)
            word = (decoded[0] << 8) | decoded[1]
            print(f"  XOR 0x{pattern:02x}: {decoded.hex()} = {word:04x}")
            
            if decoded[0] == 0x54:
                print(f"    *** FOUND 54xx PATTERN! ***")
        
        # Try rotating XOR
        print(f"\nTrying rotating XOR patterns:")
        for p1 in [0x80, 0xa9, 0xcc]:
            for p2 in [0x80, 0xa9, 0xd6]:
                if p1 != p2:
                    pattern = [p1, p2, p1, p2]
                    decoded = bytes(v0[i] ^ pattern[i] for i in range(4))
                    word = (decoded[0] << 8) | decoded[1]
                    print(f"  XOR [{p1:02x},{p2:02x}]: {decoded.hex()} = {word:04x}")
                    
                    if decoded[0] == 0x54:
                        print(f"    *** FOUND 54xx PATTERN! ***")
        
        print()

def main():
    if len(sys.argv) < 2:
        print("Usage: python final_decoder.py <clean_firmware.bin>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    try:
        with open(filename, 'rb') as f:
            clean_data = f.read()
        
        print(f"Loaded {len(clean_data)} bytes of clean firmware data")
        print()
        
        decoder = FinalDecoder()
        
        # Test XOR patterns first
        decoder.analyze_xor_patterns(clean_data)
        
        # Test vector decoding
        decoder.test_vector_decode(clean_data)
        
        # Try full decode
        print("Attempting full decode...")
        decoded = decoder.decode_firmware(clean_data)
        
        # Save result
        output_file = filename.replace('.bin', '_final_decoded.bin')
        with open(output_file, 'wb') as f:
            f.write(decoded)
        print(f"Saved decoded firmware to {output_file}")
        
        # Show hex dump of decoded vector table
        print(f"\nDecoded vector table hex dump:")
        for i in range(0, min(160, len(decoded)), 16):
            hex_part = " ".join(f"{decoded[i+j]:02x}" for j in range(min(16, len(decoded)-i)))
            addr = f"{i:04x}"
            print(f"{addr}: {hex_part}")
        
    except FileNotFoundError:
        print(f"File not found: {filename}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
