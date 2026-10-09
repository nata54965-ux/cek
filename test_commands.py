"""Check the actual GUI argument construction against the utility parser."""
import ast
from pathlib import Path
import unittest

import utility


class CommandContractTest(unittest.TestCase):
    def test_all_gui_options_are_accepted(self):
        tree = ast.parse(Path('gui.py').read_text(encoding='utf-8'))
        options = {}
        args_expression = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    if target.id in ('COMMANDS', 'NETWORKS', 'TX_LIMITS'):
                        options[target.id] = ast.literal_eval(node.value)
                    elif target.id == 'args':
                        args_expression = ast.Expression(node.value)
        self.assertIsNotNone(args_expression)
        code = compile(args_expression, 'gui.py', 'eval')
        parser = utility.build_parser()
        import sys
        for title, command in options['COMMANDS'].items():
            for network in options['NETWORKS']:
                self.assertIn(network, utility.COMMANDS[command])
                for limit in options['TX_LIMITS']:
                    for cache in (True, False):
                        for keys_args in ([], ['--keys-file', 'keys with spaces.txt']):
                            args = eval(code, {**options, 'sys': sys, 'UTILITY': 'utility.py',
                                'cmd_title': title, 'network': network, 'tx_limit': limit,
                                'use_cache': cache, 'inputs': ['one.txt', 'two with spaces.txt'],
                                'out': 'output with spaces.json', 'keys_args': keys_args})
                            parsed = parser.parse_args(args[2:])
                            self.assertEqual(parsed.command, command)
                            self.assertEqual(parsed.network, network)
                            self.assertEqual(parsed.tx_limit, limit)
                            self.assertEqual(parsed.input, ['one.txt', 'two with spaces.txt'])
                            self.assertEqual(parsed.out, 'output with spaces.json')
                            self.assertEqual(parsed.use_cache, int(cache))
                            self.assertEqual(parsed.keys_file, keys_args[-1] if keys_args else None)


if __name__ == '__main__':
    unittest.main()
