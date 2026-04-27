#!/usr/bin/env python3
"""
Firmware Analyzer for Applied Motion Products MC56F8345 update files
Analyzes the frame structure and extracts the payload data
"""

import sys
import struct
from collections import Counter

class FirmwareAnalyzer:
    def __init__(self, filename):
        with open(filename, 'rb') as f:
            self.data = f.read()
        
        # Parse header
        self.header = self.data[:13]
        self.payload = self.data[13:]
        
        # Frame marker from analysis
        self.frame_marker = bytes.fromhex('995687689c661ba5')
        
        self.analyze_structure()
    
    def analyze_structure(self):
        """Find all frame boundaries and extract structure"""
        print(f"File size: {len(self.data)} bytes")
        print(f"Header: {self.header}")
        print(f"Payload size: {len(self.payload)} bytes")
        
        # Find frame marker positions
        self.marker_positions = []
        i = 0
        while i <= len(self.payload) - 8:
            if self.payload[i:i+8] == self.frame_marker:
                self.marker_positions.append(i)
                i += 8
            else:
                i += 1
        
        print(f"Found {len(self.marker_positions)} frame markers")
        print(f"First marker at offset: {self.marker_positions[0]}")
        print(f"Last marker at offset: {self.marker_positions[-1]}")
        
        # Analyze frame sizes
        if len(self.marker_positions) > 1:
            frame_sizes = []
            for i in range(len(self.marker_positions) - 1):
                size = self.marker_positions[i+1] - self.marker_positions[i]
                frame_sizes.append(size)
            
            size_counter = Counter(frame_sizes)
            print(f"Frame sizes: {size_counter}")
            
            self.typical_frame_size = size_counter.most_common(1)[0][0]
            print(f"Typical frame size: {self.typical_frame_size} bytes")
    
    def extract_payload_data(self):
        """Extract just the payload data, removing frame markers"""
        payload_blocks = []
        
        # Pre-first-marker data (40 bytes based on analysis)
        pre_marker_data = self.payload[:self.marker_positions[0]]
        payload_blocks.append(pre_marker_data)
        print(f"Pre-marker data: {len(pre_marker_data)} bytes")
        
        # Extract data from each frame (skip the 8-byte marker at start)
        for i, marker_pos in enumerate(self.marker_positions):
            if i < len(self.marker_positions) - 1:
                frame_start = marker_pos + 8  # Skip 8-byte marker
                frame_end = self.marker_positions[i+1]
                frame_data = self.payload[frame_start:frame_end]
            else:
                # Last frame - take rest of payload
                frame_start = marker_pos + 8
                frame_data = self.payload[frame_start:]
            
            payload_blocks.append(frame_data)
            if i < 5:  # Show first few frame sizes
                print(f"Frame {i} data: {len(frame_data)} bytes")
        
        # Combine all payload data
        self.clean_payload = b''.join(payload_blocks)
        print(f"Total clean payload: {len(self.clean_payload)} bytes")
        print(f"Expected decoded size (÷2): {len(self.clean_payload)//2} bytes = {len(self.clean_payload)//2/1024:.1f} KB")
        
        return self.clean_payload
    
    def save_clean_payload(self, output_filename):
        """Save the de-framed payload to a file"""
        clean_data = self.extract_payload_data()
        with open(output_filename, 'wb') as f:
            f.write(clean_data)
        print(f"Saved clean payload to {output_filename}")
    
    def analyze_vector_table(self):
        """Analyze the first part of payload as vector table"""
        clean_data = self.extract_payload_data()
        
        print("\n=== Vector Table Analysis ===")
        print("First 81 vector entries (4 encoded bytes each = 1 decoded word):")
        print("Vector entries are JSR/JMP instructions: 54 e1/e2 XX XX")
        print()
        
        for i in range(min(81, len(clean_data)//4)):
            vector_bytes = clean_data[i*4:(i+1)*4]
            if len(vector_bytes) == 4:
                print(f"V{i:2d}: {vector_bytes.hex()}")
                
                # Show pattern analysis for first few
                if i < 10:
                    hi_nibbles = [(b>>4)&0xf for b in vector_bytes]
                    lo_nibbles = [b&0xf for b in vector_bytes]
                    print(f"     Hi: {hi_nibbles}, Lo: {lo_nibbles}")
    
    def hexdump_clean(self, start=0, length=512):
        """Create a hexdump of the clean payload"""
        clean_data = self.extract_payload_data()
        
        print(f"\n=== Hexdump of clean payload (offset {start}, {length} bytes) ===")
        
        for i in range(start, min(start + length, len(clean_data)), 16):
            # Address
            addr = f"{i:08x}"
            
            # Hex bytes
            hex_part = ""
            ascii_part = ""
            for j in range(16):
                if i + j < len(clean_data):
                    byte = clean_data[i + j]
                    hex_part += f"{byte:02x} "
                    ascii_part += chr(byte) if 32 <= byte <= 126 else "."
                else:
                    hex_part += "   "
                
                if j == 7:  # Add extra space in middle
                    hex_part += " "
            
            print(f"{addr}  {hex_part} |{ascii_part}|")
    
    def analyze_encoding_patterns(self):
        """Analyze the encoding patterns we discovered"""
        clean_data = self.extract_payload_data()
        
        print("\n=== Encoding Pattern Analysis ===")
        
        # Analyze nibble distributions by position (mod 4)
        for pos in range(4):
            hi_counter = Counter()
            lo_counter = Counter()
            
            for i in range(pos, len(clean_data), 4):
                if i < len(clean_data):
                    byte = clean_data[i]
                    hi_counter[(byte >> 4) & 0xf] += 1
                    lo_counter[byte & 0xf] += 1
            
            print(f"Position {pos} (mod 4):")
            hi_values = sorted(hi_counter.keys())
            print(f"  Hi nibbles: {[f'{n:x}' for n in hi_values]} (count: {len(hi_values)})")
            print(f"  Most common hi: {hi_counter.most_common(3)}")
            print(f"  Lo nibbles: {len(lo_counter)} distinct values")
        
        # Look for the repeating JSR pattern
        print(f"\nLooking for repeating 4-byte patterns (should be JSR to same handler):")
        pattern_counter = Counter()
        for i in range(0, len(clean_data), 4):
            if i + 4 <= len(clean_data):
                pattern = clean_data[i:i+4]
                pattern_counter[pattern] += 1
        
        print("Most common 4-byte patterns:")
        for pattern, count in pattern_counter.most_common(10):
            print(f"  {pattern.hex()}: {count} times")

def main():
    if len(sys.argv) != 2:
        print("Usage: python firmware_analyzer.py <firmware_file>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    try:
        analyzer = FirmwareAnalyzer(filename)
        
        # Save clean payload
        clean_filename = filename.replace('.e', '_clean.bin')
        analyzer.save_clean_payload(clean_filename)
        
        # Analyze vector table
        analyzer.analyze_vector_table()
        
        # Show hexdump
        analyzer.hexdump_clean(0, 256)
        
        # Analyze encoding patterns
        analyzer.analyze_encoding_patterns()
        
    except FileNotFoundError:
        print(f"Error: File {filename} not found")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
