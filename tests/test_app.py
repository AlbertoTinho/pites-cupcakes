from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app


class ShopTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.db')
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': self.path})
        result = self.app.test_cli_runner().invoke(args=['init-db', '--database', self.path, '--seed'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def post(self, url, data=None, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            session['csrf'] = 'testing-token'
        return client.post(url, data={**(data or {}), 'csrf': 'testing-token'}, follow_redirects=True)

    def register_login(self, email='cliente@example.com', client=None):
        client = client or self.client
        response = self.post('/cadastro', dict(nome='Cliente', email=email, telefone='11999999999', senha='segredo123', perfil='administrador'), client)
        self.assertIn('Cadastro realizado', response.text)
        self.post('/login', dict(email=email, senha='segredo123'), client)

    def query(self, sql, params=()):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            return connection.execute(sql, params).fetchall()

    def prepare(self, qty=2):
        self.post('/carrinho', dict(produto_id=1, quantidade=qty, acao='adicionar'))
        response = self.post('/finalizar', dict(endereco='Rua das Flores, 123, Centro, São Paulo, SP'))
        self.assertIn('Revise seu pedido', response.text)

    def test_purchase_and_privacy(self):
        self.register_login()
        user = self.query('SELECT perfil,senha_hash FROM usuarios')[0]
        self.assertEqual(user[0], 'cliente')
        self.assertNotEqual(user[1], 'segredo123')
        self.prepare()
        with self.client.session_transaction() as session:
            self.assertEqual(session['cart'], {'1': 2})
        response = self.post('/confirmar')
        self.assertIn('Pedido #1', response.text)
        self.assertEqual(self.query('SELECT estoque FROM produtos WHERE id=1')[0][0], 18)
        self.assertEqual(self.query('SELECT total_centavos,status FROM pedidos')[0], (2400, 'confirmado_simulado'))
        self.assertEqual(self.query('SELECT quantidade,preco_unitario_centavos FROM itens_pedido')[0], (2, 1200))
        self.assertIn('Pedido #1', self.client.get('/pedidos').text)
        self.post('/confirmar')  # Duplo envio não cria outro pedido.
        self.assertEqual(len(self.query('SELECT id FROM pedidos')), 1)
        other = self.app.test_client()
        self.register_login('outro@example.com', other)
        self.assertEqual(other.get('/pedidos/1').status_code, 404)
        self.assertNotIn('Pedido #1', other.get('/pedidos').text)
        self.assertEqual(other.get('/admin').status_code, 403)
        self.assertEqual(self.post('/admin/produtos/1', {}, other).status_code, 403)
        self.assertEqual(self.post('/admin/pedidos', dict(pedido_id=1, status='entregue'), other).status_code, 403)

    def test_insufficient_stock_and_rollback(self):
        self.register_login()
        self.prepare()
        self.query('UPDATE produtos SET estoque=1 WHERE id=1')
        response = self.post('/confirmar')
        self.assertIn('Estoque insuficiente', response.text)
        self.assertEqual(self.query('SELECT * FROM pedidos'), [])
        self.assertEqual(self.query('SELECT * FROM itens_pedido'), [])
        self.assertEqual(self.query('SELECT estoque FROM produtos WHERE id=1')[0][0], 1)

    def test_database_failure_rolls_back_all_items(self):
        self.register_login()
        self.post('/carrinho', dict(produto_id=2, quantidade=1))
        self.prepare()
        self.query("CREATE TRIGGER fail_item BEFORE INSERT ON itens_pedido WHEN NEW.produto_id=1 BEGIN SELECT RAISE(ABORT, 'test'); END")
        self.assertIn('Não foi possível gravar', self.post('/confirmar').text)
        self.assertEqual(self.query('SELECT * FROM pedidos'), [])
        self.assertEqual(self.query('SELECT * FROM itens_pedido'), [])
        self.assertEqual(self.query('SELECT estoque FROM produtos ORDER BY id'), [(20,), (15,), (10,)])

    def test_validation_cart_and_admin(self):
        self.assertEqual(self.client.post('/sair').status_code, 400)
        self.register_login()
        response = self.post('/cadastro', dict(nome='Outro', email='CLIENTE@example.com', telefone='123', senha='segredo123'))
        self.assertIn('já está cadastrado', response.text)
        self.post('/carrinho', dict(produto_id=1, quantidade=999))
        with self.client.session_transaction() as session:
            self.assertEqual(session.get('cart', {}), {})
        self.post('/carrinho', dict(produto_id=1, quantidade=2))
        self.post('/carrinho', dict(produto_id=1, quantidade=3))
        self.assertIn('R$ 36,00', self.client.get('/carrinho').text)
        self.post('/carrinho', dict(produto_id=1, quantidade=0))
        self.assertIn('está vazio', self.client.get('/carrinho').text)
        self.assertEqual(self.client.post('/sair').status_code, 400)
        result = self.app.test_cli_runner().invoke(args=['create-admin', '--nome', 'Admin', '--email', 'admin@example.com', '--password', 'admin12345'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.post('/login', dict(email='admin@example.com', senha='admin12345'))
        for path in ('/', '/produto/1', '/admin', '/admin/produtos/novo', '/admin/produtos/1', '/admin/pedidos'):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.post('/admin/produtos/novo', dict(nome='Novo', descricao='Teste', sabor='Limão', preco='15,25', estoque=4, ativo='on'))
        self.assertEqual(self.query('SELECT preco_centavos FROM produtos WHERE nome=?', ('Novo',))[0][0], 1525)
        self.query("INSERT INTO pedidos (usuario_id,endereco_entrega,total_centavos) VALUES (1,'Rua Teste, 123',1200)")
        self.post('/admin/pedidos', dict(pedido_id=1, status='em_preparo'))
        self.assertEqual(self.query('SELECT status FROM pedidos')[0][0], 'em_preparo')
        self.post('/admin/produtos/1', dict(nome='Chocolate', descricao='Teste', sabor='Chocolate', preco='12,00', estoque=0))
        self.assertEqual(self.client.get('/produto/1').status_code, 404)
        self.assertIn('estoque insuficiente', self.post('/carrinho', dict(produto_id=1, quantidade=1)).text)

    def test_init_preserves_existing(self):
        original = Path(self.path).read_bytes()
        result = self.app.test_cli_runner().invoke(args=['init-db', '--database', self.path, '--seed'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(Path(self.path).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
