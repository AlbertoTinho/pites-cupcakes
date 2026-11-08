import os
import secrets
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
STATUSES = ('confirmado_simulado', 'em_preparo', 'saiu_para_entrega', 'entregue', 'cancelado')


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(DATABASE=os.environ.get('FLASK_DATABASE', str(ROOT / 'BD' / 'pitesoftware_cupcakes.db')),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=65536)
    if config:
        app.config.update(config)
    if not app.config.get('SECRET_KEY'):
        key = os.environ.get('SECRET_KEY')
        if not key:
            Path(app.instance_path).mkdir(exist_ok=True)
            path = Path(app.instance_path) / 'secret.key'
            try:
                with path.open('x') as file:
                    file.write(secrets.token_hex(32))
            except FileExistsError:
                pass
            key = path.read_text()
        app.config['SECRET_KEY'] = key

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=15)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys = ON')
        return g.db

    @app.teardown_appcontext
    def close_db(error=None):
        connection = g.pop('db', None)
        if connection:
            connection.close()

    def token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return session['csrf']

    @app.before_request
    def load_user():
        g.user = db().execute('SELECT * FROM usuarios WHERE id = ?', (session['user_id'],)).fetchone() if session.get('user_id') else None
        if request.method == 'POST' and (not session.get('csrf') or not secrets.compare_digest(session['csrf'], request.form.get('csrf', ''))):
            abort(400, 'Formulário expirado. Recarregue a página e tente novamente.')

    @app.context_processor
    def helpers():
        return dict(csrf_token=token, statuses=STATUSES)

    @app.template_filter('money')
    def money(value):
        return 'R$ ' + f'{value / 100:.2f}'.replace('.', ',')

    def protected(admin=False):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not g.user:
                    return redirect(url_for('login'))
                if admin and g.user['perfil'] != 'administrador':
                    abort(403)
                return fn(*args, **kwargs)
            return wrapped
        return decorator

    def cart_items():
        items = []
        for pid, qty in session.get('cart', {}).items():
            product = db().execute('SELECT * FROM produtos WHERE id = ?', (pid,)).fetchone()
            if product:
                items.append(dict(product=product, qty=qty, subtotal=qty * product['preco_centavos']))
        return items, sum(i['subtotal'] for i in items)

    @app.route('/')
    def catalog():
        return render_template('catalog.html', products=db().execute('SELECT * FROM produtos WHERE ativo = 1 ORDER BY nome').fetchall())

    @app.route('/produto/<int:pid>')
    def product(pid):
        item = db().execute('SELECT * FROM produtos WHERE id = ? AND ativo = 1', (pid,)).fetchone()
        if not item:
            abort(404)
        return render_template('product.html', product=item)

    @app.route('/cadastro', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            name, email, phone = (request.form.get(k, '').strip() for k in ('nome', 'email', 'telefone'))
            password = request.form.get('senha', '')
            if not name or len(name) > 120 or not email or '@' not in email or len(email) > 254 or not phone or len(phone) > 30 or len(password) < 8:
                flash('Preencha nome, e-mail e telefone válidos e senha de pelo menos oito caracteres.', 'error')
            else:
                try:
                    with db():
                        db().execute('INSERT INTO usuarios (nome,email,telefone,senha_hash,perfil) VALUES (?,?,?,?,?)',
                                     (name, email.lower(), phone, generate_password_hash(password), 'cliente'))
                    flash('Cadastro realizado. Entre com seu e-mail e senha.')
                    return redirect(url_for('login'))
                except sqlite3.IntegrityError:
                    flash('Este e-mail já está cadastrado.', 'error')
        return render_template('auth.html', register=True)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            user = db().execute('SELECT * FROM usuarios WHERE email = ?', (request.form.get('email', '').strip().lower(),)).fetchone()
            if user and check_password_hash(user['senha_hash'], request.form.get('senha', '')):
                cart = session.get('cart', {})
                session.clear()
                session.update(user_id=user['id'], cart=cart)
                return redirect(url_for('catalog'))
            flash('E-mail ou senha incorretos.', 'error')
        return render_template('auth.html', register=False)

    @app.post('/sair')
    def logout():
        session.clear()
        return redirect(url_for('catalog'))

    @app.route('/carrinho', methods=['GET', 'POST'])
    def cart():
        if request.method == 'POST':
            try:
                pid = int(request.form['produto_id'])
                qty = int(request.form['quantidade'])
                basket = dict(session.get('cart', {}))
                if request.form.get('acao') == 'adicionar':
                    qty += basket.get(str(pid), 0)
                item = db().execute('SELECT * FROM produtos WHERE id = ?', (pid,)).fetchone()
                if qty < 0 or qty > 999 or (qty and (not item or not item['ativo'] or qty > item['estoque'])):
                    raise ValueError()
                if qty:
                    basket[str(pid)] = qty
                else:
                    basket.pop(str(pid), None)
                session['cart'] = basket
                session.pop('checkout', None)
                flash('Carrinho atualizado.')
            except (KeyError, ValueError):
                flash('Quantidade inválida ou estoque insuficiente.', 'error')
            return redirect(url_for('cart'))
        items, total = cart_items()
        return render_template('cart.html', items=items, total=total)

    @app.route('/finalizar', methods=['GET', 'POST'])
    @protected()
    def checkout():
        items, total = cart_items()
        if not items:
            flash('Adicione produtos ao carrinho.', 'error')
            return redirect(url_for('cart'))
        if request.method == 'POST':
            address = request.form.get('endereco', '').strip()
            if len(address) < 10 or len(address) > 500:
                flash('Informe um endereço completo entre 10 e 500 caracteres.', 'error')
            else:
                session['checkout'] = dict(address=address, cart=session.get('cart', {}), total=total)
                return render_template('checkout.html', items=items, total=total, review=True, address=address)
        return render_template('checkout.html', items=items, total=total, review=False)

    @app.post('/confirmar')
    @protected()
    def confirm():
        review = session.get('checkout')
        if not review or not review['cart'] or review['cart'] != session.get('cart'):
            flash('Revise seu pedido antes de confirmar.', 'error')
            return redirect(url_for('checkout'))
        connection = db()
        try:
            connection.execute('BEGIN IMMEDIATE')
            items, total = cart_items()
            if len(items) != len(review['cart']) or total != review['total']:
                raise ValueError('O catálogo mudou. Revise seu pedido novamente.')
            for item in items:
                p = item['product']
                if not p['ativo'] or p['estoque'] < item['qty']:
                    raise ValueError('Estoque insuficiente ou produto indisponível. Atualize seu carrinho.')
            order = connection.execute('INSERT INTO pedidos (usuario_id,endereco_entrega,status,total_centavos) VALUES (?,?,?,?)',
                                       (g.user['id'], review['address'], 'confirmado_simulado', total)).lastrowid
            for item in items:
                p = item['product']
                connection.execute('INSERT INTO itens_pedido (pedido_id,produto_id,quantidade,preco_unitario_centavos) VALUES (?,?,?,?)',
                                   (order, p['id'], item['qty'], p['preco_centavos']))
                result = connection.execute('UPDATE produtos SET estoque = estoque - ? WHERE id = ? AND ativo = 1 AND estoque >= ?', (item['qty'], p['id'], item['qty']))
                if result.rowcount != 1:
                    raise ValueError('Estoque insuficiente. Atualize seu carrinho.')
            connection.commit()
        except (ValueError, sqlite3.Error) as error:
            connection.rollback()
            session.pop('checkout', None)
            flash(str(error) if isinstance(error, ValueError) else 'Não foi possível gravar o pedido. Nada foi cobrado ou descontado. Tente novamente.', 'error')
            return redirect(url_for('cart'))
        session.pop('checkout', None)
        session['cart'] = {}
        flash(f'Pedido #{order} confirmado com pagamento simulado. Nenhuma cobrança real foi realizada.')
        return redirect(url_for('order_detail', oid=order))

    @app.route('/pedidos')
    @protected()
    def orders():
        return render_template('orders.html', orders=db().execute('SELECT * FROM pedidos WHERE usuario_id = ? ORDER BY id DESC', (g.user['id'],)).fetchall(), admin=False)

    @app.route('/pedidos/<int:oid>')
    @protected()
    def order_detail(oid):
        order = db().execute('SELECT * FROM pedidos WHERE id = ? AND usuario_id = ?', (oid, g.user['id'])).fetchone()
        if not order:
            abort(404)
        items = db().execute('SELECT i.*, p.nome FROM itens_pedido i JOIN produtos p ON p.id=i.produto_id WHERE i.pedido_id = ?', (oid,)).fetchall()
        return render_template('order.html', order=order, items=items)

    @app.route('/admin')
    @protected(admin=True)
    def admin():
        return render_template('admin.html', products=db().execute('SELECT * FROM produtos ORDER BY id').fetchall())

    @app.route('/admin/produtos/novo', methods=['GET', 'POST'])
    @app.route('/admin/produtos/<int:pid>', methods=['GET', 'POST'])
    @protected(admin=True)
    def edit_product(pid=None):
        item = db().execute('SELECT * FROM produtos WHERE id = ?', (pid,)).fetchone() if pid else None
        if pid and not item:
            abort(404)
        if request.method == 'POST':
            try:
                name = request.form.get('nome', '').strip()
                price = Decimal(request.form['preco'].replace(',', '.')) * 100
                stock = int(request.form['estoque'])
                if not name or len(name) > 120 or not price.is_finite() or price < 0 or price > 100000000 or price != price.to_integral_value() or stock < 0 or stock > 1000000:
                    raise ValueError()
                values = (name, request.form.get('descricao', '').strip(), request.form.get('sabor', '').strip(), int(price), stock, int('ativo' in request.form))
                with db():
                    if pid:
                        db().execute('UPDATE produtos SET nome=?,descricao=?,sabor=?,preco_centavos=?,estoque=?,ativo=? WHERE id=?', values + (pid,))
                    else:
                        db().execute('INSERT INTO produtos (nome,descricao,sabor,preco_centavos,estoque,ativo) VALUES (?,?,?,?,?,?)', values)
                flash('Produto salvo.')
                return redirect(url_for('admin'))
            except (KeyError, ValueError, InvalidOperation):
                flash('Confira nome, preço (até duas casas decimais) e estoque inteiro não negativo.', 'error')
        return render_template('edit_product.html', product=item)

    @app.route('/admin/pedidos', methods=['GET', 'POST'])
    @protected(admin=True)
    def admin_orders():
        if request.method == 'POST':
            status = request.form.get('status')
            if status not in STATUSES:
                abort(400)
            with db():
                updated = db().execute('UPDATE pedidos SET status=? WHERE id=?', (status, request.form.get('pedido_id')))
                if not updated.rowcount:
                    abort(404)
            flash('Status atualizado. Esta alteração não movimenta o estoque.')
            return redirect(url_for('admin_orders'))
        return render_template('orders.html', orders=db().execute('SELECT * FROM pedidos ORDER BY id DESC').fetchall(), admin=True)

    @app.cli.command('init-db')
    @click.option('--database', required=True, type=click.Path())
    @click.option('--seed', is_flag=True, help='Inclui produtos fictícios.')
    def init_db(database, seed):
        """Cria exclusivamente um banco novo; nunca substitui um arquivo existente."""
        target = Path(database).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('xb'):
                pass
        except FileExistsError:
            raise click.ClickException('O arquivo já existe. Escolha outro caminho; o banco foi preservado.')
        with closing(sqlite3.connect(target)) as connection, connection:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.executescript((ROOT / 'BD' / 'schema.sql').read_text(encoding='utf-8-sig'))
            if seed:
                connection.executemany('INSERT INTO produtos (nome,descricao,sabor,preco_centavos,estoque) VALUES (?,?,?,?,?)', [
                    ('Chocolate intenso', 'Massa macia com cobertura cremosa de chocolate.', 'Chocolate', 1200, 20),
                    ('Morango especial', 'Baunilha com cobertura delicada de morango.', 'Morango', 1400, 15),
                    ('Doce de leite', 'Um clássico com recheio generoso e toque de canela.', 'Doce de leite', 1350, 10)])
        click.echo(f'Banco novo criado: {target}')

    @app.cli.command('create-admin')
    @click.option('--nome', prompt=True)
    @click.option('--email', prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(nome, email, password):
        """Cria uma nova conta administrativa local, sem promover clientes."""
        if len(password) < 8 or not nome.strip() or '@' not in email:
            raise click.ClickException('Nome e e-mail válidos e senha com pelo menos oito caracteres são obrigatórios.')
        try:
            with db():
                db().execute('INSERT INTO usuarios (nome,email,senha_hash,perfil) VALUES (?,?,?,?)', (nome.strip(), email.strip().lower(), generate_password_hash(password), 'administrador'))
        except sqlite3.IntegrityError:
            raise click.ClickException('E-mail já cadastrado. Use um novo e-mail.')
        click.echo('Administrador criado.')

    return app


if __name__ == '__main__':
    create_app().run()
