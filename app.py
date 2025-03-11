from flask import Flask, render_template, request, redirect, url_for
import re
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from datetime import datetime
import json

# Carrega variáveis de ambiente do arquivo .env (em desenvolvimento)s
load_dotenv()

app = Flask(__name__)

# Configuração do banco de dados
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    """Estabelece conexão com o banco de dados PostgreSQL."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn

def parse_inventory_file(file_path):
    """Analisa o arquivo de inventário e retorna uma lista de produtos."""
    products = []
    
    with open(file_path, 'r', encoding='utf-8') as file:
        for line_number, line in enumerate(file, 1):
            line = line.strip()
            if not line or len(line) < 3:  # Ignora linhas muito curtas
                continue
                
            try:
                # Tenta extrair informações usando diferentes padrões
                
                # Padrão 1: quantidade no início, preço no final
                match = re.match(r'(\d+)\s+(.*?)(?:\s+(\d+(?:[.,]\d+)?))?$', line)
                
                # Padrão 2: para linhas sem quantidade explícita
                if not match:
                    match = re.match(r'(.*?)(?:\s+(\d+(?:[.,]\d+)?))?$', line)
                    if match:
                        name = match.group(1).strip()
                        price_str = match.group(2)
                        quantity = 1  # Assume quantidade 1 se não especificada
                    else:
                        # Se não conseguir extrair preço, assume que é apenas o nome
                        name = line
                        price_str = None
                        quantity = 1
                else:
                    quantity = int(match.group(1))
                    name = match.group(2).strip()
                    price_str = match.group(3)
                
                # Trata casos onde o preço pode estar ausente ou ser um '?'
                if price_str:
                    # Substitui vírgula por ponto para conversão correta
                    price_str = price_str.replace(',', '.')
                    try:
                        price = float(price_str)
                    except ValueError:
                        price = 0.0
                else:
                    price = 0.0
                
                # Limpa o nome do produto
                name = name.strip()
                if name.endswith('.'):
                    name = name[:-1].strip()
                
                # Adiciona o produto apenas se tiver um nome válido
                if name and not name.isspace():
                    products.append({
                        'quantity': quantity,
                        'name': name,
                        'price': price
                    })
                    
            except Exception as e:
                print(f"Erro ao processar linha {line_number}: '{line}'. Erro: {str(e)}")
    
    print(f"Total de produtos importados: {len(products)}")
    return products

def init_db():
    """Inicializa o banco de dados."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Cria a tabela de produtos se não existir
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL
    )
    ''')
    
    # Cria a tabela de transações com todas as colunas necessárias
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY,
        product_id INTEGER,
        type TEXT NOT NULL,
        quantity INTEGER DEFAULT 1,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    ''')
    
    cursor.close()
    conn.close()

def import_products_to_db(products):
    """Importa produtos para o banco de dados."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Verifica se já existem produtos no banco
    cursor.execute('SELECT COUNT(*) FROM products')
    count = cursor.fetchone()[0]
    
    # Só importa se não houver produtos
    if count == 0:
        for product in products:
            cursor.execute(
                'INSERT INTO products (name, quantity, price) VALUES (%s, %s, %s)',
                (product['name'], product['quantity'], product['price'])
            )
    
    cursor.close()
    conn.close()

def clear_database():
    """Limpa todas as tabelas do banco de dados."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Limpa a tabela de produtos
        cursor.execute('DELETE FROM products')
        
        # Confirma as alterações
        conn.commit()
        print("Banco de dados limpo com sucesso.")
    except Exception as e:
        conn.rollback()
        print(f"Erro ao limpar o banco de dados: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.route('/')
def index():
    """Página principal que exibe o inventário."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute('SELECT * FROM products ORDER BY name')
    products = cursor.fetchall()
    
    cursor.close()
    conn.close()
    return render_template('index.html', products=products)

@app.route('/update/<int:product_id>', methods=['POST'])
def update_product(product_id):
    """Atualiza a quantidade de um produto."""
    new_quantity = int(request.form.get('quantity', 0))
    transaction_type = request.form.get('type')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Obtém a quantidade atual
    cursor.execute('SELECT quantity FROM products WHERE id = %s', (product_id,))
    current_quantity = cursor.fetchone()[0]
    
    # Calcula a nova quantidade
    if transaction_type == 'add':
        updated_quantity = current_quantity + new_quantity
    else:  # remove
        updated_quantity = max(0, current_quantity - new_quantity)
    
    # Atualiza o produto
    cursor.execute(
        'UPDATE products SET quantity = %s WHERE id = %s',
        (updated_quantity, product_id)
    )
    
    # Registra a transação com quantidade e data atual
    cursor.execute(
        'INSERT INTO transactions (product_id, type, quantity, date) VALUES (%s, %s, %s, CURRENT_TIMESTAMP)',
        (product_id, transaction_type, new_quantity)
    )
    
    cursor.close()
    conn.close()
    
    return redirect(url_for('index'))

@app.route('/transactions')
def view_transactions():
    """Exibe o histórico de transações."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute('''
    SELECT t.id, p.name, t.type
    FROM transactions t
    JOIN products p ON t.product_id = p.id
    ORDER BY t.id DESC
    ''')
    
    transactions = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return render_template('transactions.html', transactions=transactions)

@app.route('/api/import', methods=['POST'])
def api_import():
    """API para importar produtos do arquivo JSON."""
    try:
        data = request.get_json()
        products = data.get('products', [])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Limpa a tabela antes de importar
        cursor.execute('DELETE FROM products')
        
        # Insere os produtos
        for product in products:
            cursor.execute(
                'INSERT INTO products (name, quantity, price) VALUES (%s, %s, %s)',
                (product['name'], product['quantity'], product['price'])
            )
        
        cursor.close()
        conn.close()
        
        return {'success': True, 'message': f'Importados {len(products)} produtos'}
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500

@app.route('/add_product', methods=['GET', 'POST'])
def add_product():
    """Adiciona um novo produto ao estoque."""
    if request.method == 'POST':
        name = request.form.get('name')
        quantity = int(request.form.get('quantity', 0))
        price = float(request.form.get('price', 0))
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT INTO products (name, quantity, price) VALUES (%s, %s, %s)',
            (name, quantity, price)
        )
        
        cursor.close()
        conn.close()
        
        return redirect(url_for('index'))
    
    return render_template('add_product.html')

@app.route('/edit_product/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    """Edita um produto existente."""
    conn = get_db_connection()
    
    if request.method == 'POST':
        name = request.form.get('name')
        quantity = int(request.form.get('quantity', 0))
        price = float(request.form.get('price', 0))
        
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE products SET name = %s, quantity = %s, price = %s WHERE id = %s',
            (name, quantity, price, product_id)
        )
        
        cursor.close()
        conn.close()
        
        return redirect(url_for('index'))
    
    # Obtém os dados do produto para exibir no formulário
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM products WHERE id = %s', (product_id,))
    product = cursor.fetchone()
    
    cursor.close()
    conn.close()
    
    return render_template('edit_product.html', product=product)

@app.route('/delete_product/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    """Remove um produto do estoque."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Primeiro remove as transações relacionadas
    cursor.execute('DELETE FROM transactions WHERE product_id = %s', (product_id,))
    
    # Depois remove o produto
    cursor.execute('DELETE FROM products WHERE id = %s', (product_id,))
    
    cursor.close()
    conn.close()
    
    return redirect(url_for('index'))

@app.route('/search')
def search_products():
    """Busca produtos pelo nome."""
    query = request.args.get('q', '')
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute('SELECT * FROM products WHERE name ILIKE %s ORDER BY name', (f'%{query}%',))
    products = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template('search_results.html', products=products, query=query)

@app.route('/dashboard')
def dashboard():
    """Exibe um dashboard com estatísticas do estoque."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Total de produtos
    cursor.execute('SELECT COUNT(*) as total FROM products')
    total_products = cursor.fetchone()['total']
    
    # Valor total do estoque
    cursor.execute('SELECT SUM(quantity * price) as total_value FROM products')
    total_value = cursor.fetchone()['total_value'] or 0
    
    # Produtos com estoque baixo (menos de 5 unidades)
    cursor.execute('SELECT * FROM products WHERE quantity < 5 ORDER BY quantity')
    low_stock = cursor.fetchall()
    
    # Produtos mais caros
    cursor.execute('SELECT * FROM products ORDER BY price DESC LIMIT 5')
    expensive_products = cursor.fetchall()
    
    # Transações recentes
    cursor.execute('''
    SELECT t.id, p.name, t.type
    FROM transactions t
    JOIN products p ON t.product_id = p.id
    ORDER BY t.id DESC
    LIMIT 10
    ''')
    recent_transactions = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template('dashboard.html', 
                          total_products=total_products,
                          total_value=total_value,
                          low_stock=low_stock,
                          expensive_products=expensive_products,
                          recent_transactions=recent_transactions)

def update_transactions_table():
    """Atualiza a estrutura da tabela transactions se necessário."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Verifica se as colunas quantity e date existem
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'transactions' 
            AND column_name IN ('quantity', 'date')
        """)
        existing_columns = [row[0] for row in cursor.fetchall()]
        
        # Adiciona a coluna quantity se não existir
        if 'quantity' not in existing_columns:
            cursor.execute("ALTER TABLE transactions ADD COLUMN quantity INTEGER DEFAULT 1")
            print("Coluna 'quantity' adicionada à tabela transactions")
        
        # Adiciona a coluna date se não existir
        if 'date' not in existing_columns:
            cursor.execute("ALTER TABLE transactions ADD COLUMN date TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            print("Coluna 'date' adicionada à tabela transactions")
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Erro ao atualizar a tabela transactions: {str(e)}")
    finally:
        cursor.close()
        conn.close()

# @app.route('/update-db', methods=['GET'])
# def update_database():
    """Endpoint para atualizar a estrutura do banco de dados."""
    try:
        update_transactions_table()
        return "Estrutura do banco de dados atualizada com sucesso!"
    except Exception as e:
        return f"Erro ao atualizar o banco de dados: {str(e)}"

# Inicialização do banco de dados e importação de produtos
if __name__ == '__main__':
    # Verifica se o banco de dados está configurado
    if not DATABASE_URL:
        print("AVISO: Variável de ambiente DATABASE_URL não configurada.")
        print("Configure a variável DATABASE_URL no arquivo .env")
        exit(1)
    
    # Inicializa o banco de dados
    init_db()
    
    # Atualiza a estrutura da tabela de transações
    update_transactions_table()
    
    # Verifica se já existem produtos no banco
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM products')
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    
    # Apenas importa produtos se o banco estiver vazio
    if count == 0:
        print("Banco de dados vazio. Importando produtos iniciais...")
        # Verifica se o arquivo de inventário existe
        inventory_file = 'Estoque Papelaria Flor de Maria_250310_215308.txt'
        if os.path.exists(inventory_file):
            # Analisa o arquivo e importa para o banco de dados
            products = parse_inventory_file(inventory_file)
            import_products_to_db(products)
            
            # Salva os produtos em um arquivo JSON para importação posterior
            with open('products.json', 'w', encoding='utf-8') as f:
                json.dump({'products': products}, f, ensure_ascii=False, indent=2)
            
            print(f"Total de produtos importados: {len(products)}")
    else:
        print(f"Banco de dados já contém {count} produtos. Importação ignorada.")
    
    # Cria a pasta templates se não existir
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Cria os templates HTML
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Controle de Estoque - Papelaria Flor de Maria</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            background-color: white;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }
        th, td {
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        th {
            background-color: #f8f9fa;
            font-weight: bold;
        }
        tr:hover {
            background-color: #f1f1f1;
        }
        .actions {
            display: flex;
            gap: 10px;
        }
        .transaction-form {
            display: flex;
            gap: 5px;
            align-items: center;
        }
        input[type="number"] {
            width: 60px;
            padding: 5px;
        }
        button {
            padding: 5px 10px;
            cursor: pointer;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 3px;
        }
        button.remove {
            background-color: #f44336;
        }
        .nav {
            display: flex;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        .nav a {
            padding: 10px 15px;
            background-color: #007bff;
            color: white;
            text-decoration: none;
            border-radius: 3px;
        }
        .search {
            margin-bottom: 20px;
        }
        .search input {
            padding: 8px;
            width: 300px;
        }
        .add-product {
            margin-bottom: 20px;
        }
        .add-product a {
            padding: 10px 15px;
            background-color: #4CAF50;
            color: white;
            text-decoration: none;
            border-radius: 3px;
            display: inline-block;
        }
        .edit-delete {
            display: flex;
            gap: 5px;
        }
        .edit-delete a {
            padding: 5px 10px;
            background-color: #FFC107;
            color: white;
            text-decoration: none;
            border-radius: 3px;
        }
        .edit-delete form button {
            background-color: #f44336;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Controle de Estoque - Papelaria Flor de Maria</h1>
        
        <div class="nav">
            <a href="/">Estoque</a>
            <a href="/transactions">Histórico de Transações</a>
            <a href="/dashboard">Dashboard</a>
        </div>
        
        <div class="search">
            <form action="/search" method="get">
                <input type="text" name="q" placeholder="Buscar produto..." value="{{ request.args.get('q', '') }}">
                <button type="submit">Buscar</button>
            </form>
        </div>
        
        <div class="add-product">
            <a href="/add_product">Adicionar Novo Produto</a>
        </div>
        
        <table id="productsTable">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Produto</th>
                    <th>Quantidade</th>
                    <th>Preço (R$)</th>
                    <th>Valor Total (R$)</th>
                    <th>Ações</th>
                </tr>
            </thead>
            <tbody>
                {% for product in products %}
                <tr>
                    <td>{{ product.id }}</td>
                    <td>{{ product.name }}</td>
                    <td>{{ product.quantity }}</td>
                    <td>{{ "%.2f"|format(product.price) }}</td>
                    <td>{{ "%.2f"|format(product.quantity * product.price) }}</td>
                    <td>
                        <div class="actions">
                            <form class="transaction-form" action="/update/{{ product.id }}" method="post">
                                <input type="number" name="quantity" min="1" value="1" required>
                                <button type="submit" name="type" value="add">Entrada</button>
                                <button type="submit" name="type" value="remove" class="remove">Saída</button>
                            </form>
                            <div class="edit-delete">
                                <a href="/edit_product/{{ product.id }}">Editar</a>
                                <form action="/delete_product/{{ product.id }}" method="post" onsubmit="return confirm('Tem certeza que deseja excluir este produto?');">
                                    <button type="submit">Excluir</button>
                                </form>
                            </div>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
        ''')
    
    with open('templates/transactions.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Histórico de Transações - Papelaria Flor de Maria</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            background-color: white;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }
        th, td {
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        th {
            background-color: #f8f9fa;
            font-weight: bold;
        }
        tr:hover {
            background-color: #f1f1f1;
        }
        .nav {
            display: flex;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        .nav a {
            padding: 10px 15px;
            background-color: #007bff;
            color: white;
            text-decoration: none;
            border-radius: 3px;
        }
        .add {
            color: green;
            font-weight: bold;
        }
        .remove {
            color: red;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Histórico de Transações - Papelaria Flor de Maria</h1>
        
        <div class="nav">
            <a href="/">Estoque</a>
            <a href="/transactions">Histórico de Transações</a>
            <a href="/dashboard">Dashboard</a>
        </div>
        
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Produto</th>
                    <th>Tipo</th>
                    <th>Quantidade</th>
                    <th>Data/Hora</th>
                </tr>
            </thead>
            <tbody>
                {% for transaction in transactions %}
                <tr>
                    <td>{{ transaction.id }}</td>
                    <td>{{ transaction.name }}</td>
                    <td class="{{ transaction.type }}">
                        {% if transaction.type == 'add' %}
                            Entrada
                        {% else %}
                            Saída
                        {% endif %}
                    </td>
                    <td>{{ transaction.quantity }}</td>
                    <td>{{ transaction.date }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
        ''')
    
    with open('templates/add_product.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Adicionar Produto - Papelaria Flor de Maria</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .container {
            max-width: 600px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input {
            width: 100%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 3px;
        }
        button {
            padding: 10px 15px;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 3px;
            cursor: pointer;
        }
        .nav {
            display: flex;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        .nav a {
            padding: 10px 15px;
            background-color: #007bff;
            color: white;
            text-decoration: none;
            border-radius: 3px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Adicionar Novo Produto</h1>
        
        <div class="nav">
            <a href="/">Voltar ao Estoque</a>
        </div>
        
        <form action="/add_product" method="post">
            <div class="form-group">
                <label for="name">Nome do Produto:</label>
                <input type="text" id="name" name="name" required>
            </div>
            
            <div class="form-group">
                <label for="quantity">Quantidade:</label>
                <input type="number" id="quantity" name="quantity" min="0" value="0" required>
            </div>
            
            <div class="form-group">
                <label for="price">Preço (R$):</label>
                <input type="number" id="price" name="price" min="0" step="0.01" value="0.00" required>
            </div>
            
            <button type="submit">Adicionar Produto</button>
        </form>
    </div>
</body>
</html>
        ''')
    
    with open('templates/edit_product.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Editar Produto - Papelaria Flor de Maria</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .container {
            max-width: 600px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }
        input {
            width: 100%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 3px;
        }
        button {
            padding: 10px 15px;
            background-color: #FFC107;
            color: white;
            border: none;
            border-radius: 3px;
            cursor: pointer;
        }
        .nav {
            display: flex;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        .nav a {
            padding: 10px 15px;
            background-color: #007bff;
            color: white;
            text-decoration: none;
            border-radius: 3px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Editar Produto</h1>
        
        <div class="nav">
            <a href="/">Voltar ao Estoque</a>
        </div>
        
        <form action="/edit_product/{{ product.id }}" method="post">
            <div class="form-group">
                <label for="name">Nome do Produto:</label>
                <input type="text" id="name" name="name" value="{{ product.name }}" required>
            </div>
            
            <div class="form-group">
                <label for="quantity">Quantidade:</label>
                <input type="number" id="quantity" name="quantity" min="0" value="{{ product.quantity }}" required>
            </div>
            
            <div class="form-group">
                <label for="price">Preço (R$):</label>
                <input type="number" id="price" name="price" min="0" step="0.01" value="{{ product.price }}" required>
            </div>
            
            <button type="submit">Salvar Alterações</button>
        </form>
    </div>
</body>
</html>
        ''')
    
    with open('templates/search_results.html', 'w', encoding='utf-8') as f:
        f.write('''
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Resultados da Busca - Papelaria Flor de Maria</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            background-color: white;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }
        th, td {
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        th {
            background-color: #f8f9fa;
            font-weight: bold;
        }
        tr:hover {
            background-color: #f1f1f1;
        }
        .actions {
            display: flex;
            gap: 10px;
        }
        .transaction-form {
            display: flex;
            gap: 5px;
            align-items: center;
        }
        input[type="number"] {
            width: 60px;
            padding: 5px;
        }
        button {
            padding: 5px 10px;
            cursor: pointer;
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 3px;
        }
        button.remove {
            background-color: #f44336;
        }
        .nav {
            display: flex;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        .nav a {
            padding: 10px 15px;
            background-color: #007bff;
            color: white;
            text-decoration: none;
            border-radius: 3px;
        }
        .search {
            margin-bottom: 20px;
        }
        .search input {
            padding: 8px;
            width: 300px;
        }
        .edit-delete {
            display: flex;
            gap: 5px;
        }
        .edit-delete a {
            padding: 5px 10px;
            background-color: #FFC107;
            color: white;
            text-decoration: none;
            border-radius: 3px;
        }
        .edit-delete form button {
            background-color: #f44336;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Resultados da Busca</h1>
        
        <div class="nav">
            <a href="/">Estoque</a>
            <a href="/transactions">Histórico de Transações</a>
            <a href="/dashboard">Dashboard</a>
        </div>
        
        <div class="search">
            <form action="/search" method="get">
                <input type="text" name="q" placeholder="Buscar produto..." value="{{ query }}">
                <button type="submit">Buscar</button>
            </form>
        </div>
        
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Produto</th>
                    <th>Quantidade</th>
                    <th>Preço (R$)</th>
                    <th>Valor Total (R$)</th>
                    <th>Ações</th>
                </tr>
            </thead>
            <tbody>
                {% for product in products %}
                <tr>
                    <td>{{ product.id }}</td>
                    <td>{{ product.name }}</td>
                    <td>{{ product.quantity }}</td>
                    <td>{{ "%.2f"|format(product.price) }}</td>
                    <td>{{ "%.2f"|format(product.quantity * product.price) }}</td>
                    <td>
                        <div class="actions">
                            <form class="transaction-form" action="/update/{{ product.id }}" method="post">
                                <input type="number" name="quantity" min="1" value="1" required>
                                <button type="submit" name="type" value="add">Entrada</button>
                                <button type="submit" name="type" value="remove" class="remove">Saída</button>
                            </form>
                            <div class="edit-delete">
                                <a href="/edit_product/{{ product.id }}">Editar</a>
                                <form action="/delete_product/{{ product.id }}" method="post" onsubmit="return confirm('Tem certeza que deseja excluir este produto?');">
                                    <button type="submit">Excluir</button>
                                </form>
                            </div>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
        ''')
    
    # Inicia o servidor Flask
    app.run(debug=True)