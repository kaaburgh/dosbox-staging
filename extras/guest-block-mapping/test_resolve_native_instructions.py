"""Safe synthetic x86-64 instruction and sample tests; no game assets."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from resolve_native_instructions import load_disassembly, resolve, classify_mnemonic


class InstructionSampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # nop; shr rax,0xc; test rax,rax; je +1; nop; ret
        self.machine = bytes.fromhex('90 48 c1 e8 0c 48 85 c0 74 01 90 c3')
        self.native = self.root/'jit-123-gen7.host.bin'
        self.native.write_bytes(self.machine)
        self.meta = {'schema': 1, 'generation': 7, 'guest_eip': '0x11216',
                     'host_bytes': len(self.machine), 'host_filename': self.native.name}
        (self.root/'jit-123-gen7.json').write_text(json.dumps(self.meta))
        self.summary = {'selected_generations': 1, 'selected_snapshot_samples': 6,
                        'blocks': [{'generation': 7, 'guest_eip': '0x11216',
                                    'samples': 6, 'native_bytes': len(self.machine),
                                    'host_sha256': hashlib.sha256(self.machine).hexdigest()}]}

    def test_full_linear_disassembly_and_instruction_starts(self):
        d = load_disassembly(self.native)
        self.assertEqual([i for i in d], [0, 1, 5, 8, 10, 11])
        self.assertEqual(d[1]['mnemonic'], 'shr')
        self.assertEqual(d[5]['family'], 'predicate')
        self.assertEqual(d[8]['family'], 'branch')

    def test_counts_and_ambiguous_mid_instruction_rip(self):
        rows = [{'generation': '7', 'offset': str(offset), 'samples': '1'}
                for offset in (0, 1, 2, 5, 8, 11)]
        counts, instructions = resolve(self.summary, rows, self.root)
        self.assertEqual(counts['total_selected_samples'], 6)
        self.assertEqual(counts['at_linear_instruction_start'], 5)
        self.assertEqual(counts['not_at_linear_instruction_start'], 1)
        self.assertEqual(sum(x['samples'] for x in instructions), 5)
        self.assertFalse(any(x['offset'] == 2 for x in instructions))

    def test_modified_raw_host_blob_fails_hash_validation(self):
        self.native.write_bytes(bytes([0x91]) + self.machine[1:])
        with self.assertRaisesRegex(ValueError, 'hash'):
            resolve(self.summary, [], self.root)

    def test_wrong_run_sample_counts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'counts'):
            resolve(self.summary, [{'generation': '7', 'offset': '0', 'samples': '1'}], self.root)

    def test_unknown_generation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown generation'):
            resolve(self.summary, [{'generation': '8', 'offset': '0', 'samples': '6'}], self.root)

    def test_absolute_operand_redaction(self):
        f = self.root / 'ptr.bin'
        f.write_bytes(bytes.fromhex('48 b8 78 56 34 12 00 00 00 00 c3'))
        ins = load_disassembly(f)
        self.assertEqual(ins[0]['mnemonic'], 'movabs')
        self.assertIn('IMM', ins[0]['operand_shape'])
        self.assertNotIn('0x12345678', ins[0]['operand_shape'])

    def test_redaction_preserves_x86_register_numbers(self):
        f = self.root / 'numbered-registers.bin'
        # mov r12,r13; mov r14d,r15d; ret
        f.write_bytes(bytes.fromhex('4d 89 ec 45 89 fe c3'))
        ins = load_disassembly(f)
        shapes = ' '.join(row['operand_shape'] for row in ins.values())
        self.assertIn('r12', shapes)
        self.assertIn('r13', shapes)
        self.assertIn('r14d', shapes)
        self.assertIn('r15d', shapes)
        self.assertNotIn('IMM', shapes)

    def test_flags_and_x87_stay_separate_from_memory_mov(self):
        self.assertEqual(classify_mnemonic('pushf'), 'flags-or-state')
        self.assertEqual(classify_mnemonic('popfq'), 'flags-or-state')
        self.assertEqual(classify_mnemonic('fxch'), 'x87')
        self.assertEqual(classify_mnemonic('lea'), 'move-or-address')

    def test_family_labels_do_not_claim_tlb_or_helper_executions(self):
        self.assertEqual(classify_mnemonic('call'), 'call-site')
        self.assertEqual(classify_mnemonic('jmp'), 'branch')
        self.assertEqual(classify_mnemonic('mov'), 'move-or-address')

if __name__ == '__main__':
    unittest.main()
