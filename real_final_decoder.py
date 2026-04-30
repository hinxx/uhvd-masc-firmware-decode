#!/usr/bin/env python3
"""
🎯 REAL FINAL MC56F8345 Decoder 
Instruction-Type-Aware Position Mapping Discovery!

Key insight: Different instruction types use different byte positions:
- Vector opcodes (54xx): positions [0] XOR 0xcd, [3] XOR 0x45  
- Regular code: positions [1], [2] directly
"""

import sys
from collections import Counter

class RealFinalDecoder:
    def __init__(self):
        self.vector_table_start = 36
        self.vector_table_end = 36 + (81 * 8)
        
        # Known vector patterns that use special decoding
        self.vector_patterns = {
            bytes.fromhex('99626ea7'): bytes([0x54, 0xe2]),  # JSR
            bytes.fromhex('99626ea4'): bytes([0x54, 0xe1]),  # JMP  
            bytes.fromhex('9560269f'): bytes([0x54, 0xe1]),  # JMP (alternate)
        }
    
    def decode_word_smart(self, encoded_word, offset=0):
        """Smart decoder that chooses method based on instruction type"""
        if len(encoded_word) != 4:
            return None
        
        # Method 1: Known vector patterns
        if encoded_word in self.vector_patterns:
            return self.vector_patterns[encoded_word]
        
        # Method 2: Check if this looks like a vector-style instruction
        # (Instructions starting with 54xx when decoded with vector method)
        vector_decoded = self.decode_vector_style(encoded_word)
        if vector_decoded and vector_decoded[0] == 0x54:
            return vector_decoded
        
        # Method 3: Regular code - use positions [1,2] 
        return self.decode_code_style(encoded_word)
    
    def decode_vector_style(self, encoded_word):
        """Vector table style: pos[0]^0xcd, pos[3]^0x45"""
        byte0 = encoded_word[0] ^ 0xcd
        byte1 = encoded_word[3] ^ 0x45
        return bytes([byte0, byte1])
    
    def decode_code_style(self, encoded_word):
        """Regular code style: pos[1], pos[2] directly"""
        return bytes([encoded_word[1], encoded_word[2]])
    
    def decode_firmware_real_final(self, clean_data):
        """The real final decode using instruction-aware mapping"""
        print("🎯 REAL FINAL DECODE: MC56F8345 Firmware")
        print("Using instruction-type-aware position mapping!")
        
        decoded_data = bytearray()
        stats = {
            'vector_patterns': 0,
            'vector_style_54xx': 0,
            'code_style': 0,
            'skipped_impostors': 0
        }
        
        for i in range(0, len(clean_data), 4):
            encoded_word = clean_data[i:i+4]
            if len(encoded_word) != 4:
                break
            
            # Skip impostor frame markers
            if encoded_word.hex().startswith(('956f26', '956e26', 'e96726', 'ee1426')):
                stats['skipped_impostors'] += 1
                continue
            
            # Decode with smart method
            if encoded_word in self.vector_patterns:
                decoded_word = self.vector_patterns[encoded_word]
                stats['vector_patterns'] += 1
            else:
                # Try vector style first
                vector_decoded = self.decode_vector_style(encoded_word)
                if vector_decoded[0] == 0x54:
                    decoded_word = vector_decoded
                    stats['vector_style_54xx'] += 1
                else:
                    # Use code style
                    decoded_word = self.decode_code_style(encoded_word)
                    stats['code_style'] += 1
            
            if decoded_word:
                decoded_data.extend(decoded_word)
        
        print(f"Decoding Statistics:")
        print(f"  Known vector patterns: {stats['vector_patterns']}")
        print(f"  Vector-style 54xx: {stats['vector_style_54xx']}")
        print(f"  Code-style: {stats['code_style']}")
        print(f"  Skipped impostors: {stats['skipped_impostors']}")
        print(f"  Total decoded: {len(decoded_data)} bytes ({len(decoded_data)/1024:.1f} KB)")
        
        return bytes(decoded_data), stats
    
    def analyze_decode_quality_comprehensive(self, decoded_data, stats):
        """Comprehensive quality analysis of the decoded firmware"""
        print(f"\\n🔍 COMPREHENSIVE QUALITY ANALYSIS")
        
        if len(decoded_data) < 200:
            print("❌ ERROR: Decoded data too small")
            return False
        
        # 1. Vector table analysis
        print("\\n📋 Vector Table Analysis:")
        vector_instructions = {'jmp_54e1': 0, 'jsr_54e2': 0, 'other_54xx': 0, 'non_54xx': 0}
        
        for i in range(0, min(160, len(decoded_data)), 4):
            if i + 1 < len(decoded_data):
                word = (decoded_data[i] << 8) | decoded_data[i+1]
                if word == 0x54e1:
                    vector_instructions['jmp_54e1'] += 1
                elif word == 0x54e2:
                    vector_instructions['jsr_54e2'] += 1
                elif (word & 0xFF00) == 0x5400:
                    vector_instructions['other_54xx'] += 1
                else:
                    vector_instructions['non_54xx'] += 1
        
        for instr_type, count in vector_instructions.items():
            print(f"  {instr_type}: {count}")
        
        # Check expected vector table structure (2 JMP + many JSR)
        vector_quality = (vector_instructions['jmp_54e1'] >= 1 and 
                         vector_instructions['jsr_54e2'] >= 10)
        print(f"  Vector quality: {'✅ GOOD' if vector_quality else '❌ POOR'}")
        
        # 2. Code section diversity analysis
        print("\\n🎲 Code Section Diversity:")
        code_start = 200
        code_sample = decoded_data[code_start:code_start+2000]
        
        if len(code_sample) > 100:
            # Analyze opcode diversity
            opcodes = [code_sample[i] for i in range(0, len(code_sample), 2)]
            opcode_counts = Counter(opcodes)
            unique_opcodes = len(opcode_counts)
            diversity_ratio = unique_opcodes / len(opcodes) if opcodes else 0
            
            print(f"  Total opcodes analyzed: {len(opcodes)}")
            print(f"  Unique opcodes: {unique_opcodes}")
            print(f"  Diversity ratio: {diversity_ratio:.3f}")
            
            # Show most common opcodes
            print(f"  Top 10 opcodes: {opcode_counts.most_common(10)}")
            
            # Check for unrealistic patterns
            count_54 = opcode_counts.get(0x54, 0)
            count_00 = opcode_counts.get(0x00, 0)
            count_ff = opcode_counts.get(0xff, 0)
            
            percent_54 = count_54 / len(opcodes) * 100
            percent_00 = count_00 / len(opcodes) * 100
            percent_ff = count_ff / len(opcodes) * 100
            
            print(f"  54xx instructions: {percent_54:.1f}% (should be <10% in code)")
            print(f"  00xx instructions: {percent_00:.1f}% (padding/data)")
            print(f"  FFxx instructions: {percent_ff:.1f}% (empty flash)")
            
            # Quality criteria
            code_quality = (diversity_ratio > 0.1 and  # Good diversity
                           percent_54 < 15 and         # Not too many vector-style
                           percent_ff < 50)            # Not mostly empty
            
            print(f"  Code quality: {'✅ GOOD' if code_quality else '❌ POOR'}")
            
            return vector_quality and code_quality
        
        return False
    
    def show_decode_samples(self, clean_data, decoded_data):
        """Show detailed decode samples for verification"""
        print(f"\\n📊 DECODE SAMPLES")
        
        sections = [
            ("Vector Table", 36, 36 + 64),
            ("Early Code", 1000, 1000 + 64),
            ("Mid Code", 5000, 5000 + 64),
        ]
        
        decoded_word_index = 0
        
        for section_name, start, end in sections:
            print(f"\\n{section_name} (offset {start:04x}):")
            samples_shown = 0
            
            for i in range(start, min(end, len(clean_data)), 4):
                if i + 4 <= len(clean_data):
                    encoded = clean_data[i:i+4]
                    
                    # Skip impostors
                    if encoded.hex().startswith(('956f26', '956e26', 'e96726', 'ee1426')):
                        continue
                    
                    # Get actual decoded word
                    if decoded_word_index * 2 + 1 < len(decoded_data):
                        actual_word = ((decoded_data[decoded_word_index * 2] << 8) | 
                                      decoded_data[decoded_word_index * 2 + 1])
                        
                        # Test our decode method
                        test_decoded = self.decode_word_smart(encoded, i)
                        test_word = ((test_decoded[0] << 8) | test_decoded[1]) if test_decoded else 0
                        
                        # Determine method used
                        if encoded in self.vector_patterns:
                            method = "VECTOR_KNOWN"
                        elif test_decoded and test_decoded[0] == 0x54:
                            method = "VECTOR_STYLE"
                        else:
                            method = "CODE_STYLE"
                        
                        match = "✅" if test_word == actual_word else "❌"
                        
                        # Add instruction type annotation
                        annotation = ""
                        if actual_word == 0x54e1:
                            annotation = " (JMP)"
                        elif actual_word == 0x54e2:
                            annotation = " (JSR)"
                        elif (actual_word & 0xFF00) == 0x5400:
                            annotation = f" (54{actual_word&0xFF:02x})"
                        
                        print(f"  {i:04x}: {encoded.hex()} -> {actual_word:04x} ({method}) {match}{annotation}")
                        
                        decoded_word_index += 1
                        samples_shown += 1
                        
                        if samples_shown >= 8:
                            break

def main():
    if len(sys.argv) < 2:
        print("Usage: python real_final_decoder.py <clean_firmware.bin>")
        sys.exit(1)
    
    filename = sys.argv[1]
    
    try:
        with open(filename, 'rb') as f:
            clean_data = f.read()
        
        print(f"🚀 MC56F8345 REAL FINAL DECODER")
        print(f"📥 Input: {len(clean_data)} bytes")
        print(f"🎯 Expected: ~{len(clean_data)//2} bytes decoded")
        
        decoder = RealFinalDecoder()
        
        # Perform the real final decode
        decoded_data, stats = decoder.decode_firmware_real_final(clean_data)
        
        # Comprehensive quality analysis
        is_high_quality = decoder.analyze_decode_quality_comprehensive(decoded_data, stats)
        
        # Show decode samples
        decoder.show_decode_samples(clean_data, decoded_data)
        
        if is_high_quality and len(decoded_data) > 10000:
            # Save the REAL final result
            output_file = filename.replace('.bin', '_REAL_FINAL_DECODED.bin')
            with open(output_file, 'wb') as f:
                f.write(decoded_data)
            
            print(f"\\n🎉🎉🎉 REAL SUCCESS! 🎉🎉🎉")
            print(f"💾 Saved to: {output_file}")
            print(f"📊 Size: {len(decoded_data)} bytes ({len(decoded_data)/1024:.1f} KB)")
            print(f"🔧 Ready for DSP56800E disassembly!")
            
            # Final hex dump
            print(f"\\n📋 FINAL FIRMWARE DUMP:")
            print("Vector Table (0x0000-0x0040):")
            for i in range(0, min(64, len(decoded_data)), 16):
                hex_part = ' '.join(f'{decoded_data[i+j]:02x}' for j in range(min(16, len(decoded_data)-i)))
                print(f"{i:04x}: {hex_part}")
            
            print("\\nCode Section (0x0200-0x0240):")
            start = 0x200
            for i in range(start, min(start+64, len(decoded_data)), 16):
                if i < len(decoded_data):
                    hex_part = ' '.join(f'{decoded_data[i+j]:02x}' for j in range(min(16, len(decoded_data)-i)))
                    print(f"{i:04x}: {hex_part}")
            
        else:
            print(f"\\n❌ FAILED: Quality analysis failed")
            print(f"🔍 May need further investigation")
        
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
