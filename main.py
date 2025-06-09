from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import sqlite3
import logging
import os
import re
import random

app = Flask(__name__)

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Lista de proxies fornecida
proxies_list = [
    {"ip": "198.23.239.134", "port": "6540", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "207.244.217.165", "port": "6712", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "107.172.163.27", "port": "6543", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "23.94.138.75", "port": "6349", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "216.10.27.159", "port": "6837", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "136.0.207.84", "port": "6661", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "64.64.118.149", "port": "6732", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "142.147.128.93", "port": "6593", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "104.239.105.125", "port": "6655", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
    {"ip": "173.0.9.70", "port": "5653", "user": "epvyevxu", "pass": "s5yxwb9rxpj1"},
]

# Função para selecionar um proxy aleatório
def get_random_proxy():
    proxy = random.choice(proxies_list)
    proxy_url = f"http://{proxy['user']}:{proxy['pass']}@{proxy['ip']}:{proxy['port']}"
    return {
        "http": proxy_url,
        "https": proxy_url
    }

# Cabeçalhos completos para simular um navegador
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# Inicialização do banco SQLite
def init_db():
    with sqlite3.connect('stock_data.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                stock INTEGER,
                price INTEGER,
                last_updated TEXT
            )
        ''')
        conn.commit()

# Função para salvar dados no banco
def save_to_db(category, items, last_updated):
    with sqlite3.connect('stock_data.db') as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM stock WHERE category = ?', (category,))
        for item in items:
            cursor.execute('''
                INSERT INTO stock (category, name, stock, price, last_updated)
                VALUES (?, ?, ?, ?, ?)
            ''', (category, item['name'], item['stock'], item.get('price', 0), last_updated))
        conn.commit()

# Função para carregar dados do banco
def load_from_db(category=None):
    with sqlite3.connect('stock_data.db') as conn:
        cursor = conn.cursor()
        if category:
            cursor.execute('SELECT name, stock, price, last_updated FROM stock WHERE category = ?', (category,))
            rows = cursor.fetchall()
            return [{'name': row[0], 'stock': row[1], 'price': row[2]} for row in rows], rows[0][3] if rows else None
        else:
            data = {'seeds': [], 'gear': [], 'egg_shop': [], 'honey': [], 'cosmetics': [], 'last_updated': None}
            for cat in ['seeds', 'gear', 'egg_shop', 'honey', 'cosmetics']:
                cursor.execute('SELECT name, stock, price, last_updated FROM stock WHERE category = ?', (cat,))
                rows = cursor.fetchall()
                data[cat] = [{'name': row[0], 'stock': row[1], 'price': row[2]} for row in rows]
                if rows and not data['last_updated']:
                    data['last_updated'] = rows[0][3]
            return data

def parse_update_time(time_text):
    """Converte texto como '03m 56s' ou '01h 13m 56s' em segundos."""
    time_text = time_text.lower().strip()
    pattern = r'(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?'
    match = re.search(pattern, time_text)
    if not match:
        return 300  # 5 minutos como padrão
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    total_seconds = hours * 3600 + minutes * 60 + seconds
    return max(total_seconds, 30)  # Mínimo de 30 segundos

def scrape_stock():
    """Raspa os dados de estoque do site."""
    url = 'https://vulcanvalues.com/grow-a-garden/stock'
    last_updated = datetime.now().isoformat()
    next_update_times = {}
    max_retries = 3  # Número máximo de tentativas com proxies diferentes

    for attempt in range(max_retries):
        proxies = get_random_proxy()
        logger.info(f"Tentativa {attempt + 1}/{max_retries} com proxy: {proxies['http']}")

        try:
            response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            logger.info(f"Status da resposta: {response.status_code}")
            logger.info(f"Tamanho do HTML: {len(response.text)} caracteres")

            new_data = {
                'seeds': [], 'gear': [], 'egg_shop': [], 'honey': [], 'cosmetics': []
            }

            # Debug: Procurar por elementos principais
            possible_containers = [
                'div.grid', 'div[class*="grid"]', 'div[class*="stock"]',
                'div[class*="container"]', 'main', 'section'
            ]
            for selector in possible_containers:
                elements = soup.select(selector)
                if elements:
                    logger.info(f"Encontrados {len(elements)} elementos com seletor: {selector}")

            # Encontrar a seção de estoques
            stock_grid = soup.find('div', class_='grid grid-cols-1 md:grid-cols-3 gap-6 px-6 text-left max-w-screen-lg mx-auto')
            if not stock_grid:
                logger.error("Seção de estoque não encontrada - tentando alternativas...")
                stock_grid = soup.find('div', class_='grid') or soup.find('main') or soup.find('section')
            if not stock_grid:
                logger.error("Nenhuma estrutura principal encontrada")
                continue

            sections_found = 0
            for section in stock_grid.find_all('div'):
                h2 = section.find('h2')
                if not h2:
                    continue
                sections_found += 1
                category = h2.text.strip().lower()
                logger.info(f"Processando categoria: {category}")

                # Procurar pelo tempo de atualização
                update_time_text = ""
                update_paragraph = section.find('p', string=re.compile(r'UPDATES IN:', re.IGNORECASE))
                if not update_paragraph:
                    for p in section.find_all(['p', 'div', 'span']):
                        if p.get_text() and 'updates in:' in p.get_text().lower():
                            update_time_text = p.get_text()
                            break
                else:
                    update_time_text = update_paragraph.get_text()

                if update_time_text:
                    time_match = re.search(r'updates in:\s*(.+)', update_time_text.lower())
                    if time_match:
                        time_str = time_match.group(1).strip()
                        update_seconds = parse_update_time(time_str)
                        logger.info(f"Categoria {category}: próxima atualização em {update_seconds}s")
                    else:
                        update_seconds = 300
                else:
                    update_seconds = 300

                if 'gear' in category:
                    category_key = 'gear'
                elif 'egg' in category:
                    category_key = 'egg_shop'
                elif 'seeds' in category:
                    category_key = 'seeds'
                elif 'honey' in category:
                    category_key = 'honey'
                elif 'cosmetics' in category:
                    category_key = 'cosmetics'
                else:
                    logger.info(f"Categoria não reconhecida: {category}")
                    continue

                next_update_times[category_key] = update_seconds

                ul = section.find('ul')
                if not ul:
                    logger.warning(f"Lista não encontrada para categoria: {category}")
                    continue

                items_found = 0
                for li in ul.find_all('li'):
                    item_text = li.get_text().strip()
                    logger.info(f"Item encontrado: {item_text}")
                    if not item_text:
                        continue
                    if ' x' in item_text:
                        parts = item_text.rsplit(' x', 1)
                        name = parts[0].strip()
                        try:
                            stock = int(parts[1].strip())
                        except (ValueError, IndexError):
                            stock = 0
                    else:
                        name = item_text.strip()
                        stock = 1
                    if name:
                        new_data[category_key].append({
                            'name': name,
                            'stock': stock,
                            'price': 0
                        })
                        items_found += 1
                logger.info(f"Categoria {category_key}: {items_found} itens encontrados")

            logger.info(f"Total de seções processadas: {sections_found}")
            total_items = sum(len(items) for items in new_data.values())
            logger.info(f"Total de itens coletados: {total_items}")

            # Salvar no banco
            for category, items in new_data.items():
                save_to_db(category, items, last_updated)
            logger.info(f"Dados salvos no banco: {last_updated}")

            # Reagendar baseado no menor tempo
            if next_update_times:
                min_seconds = min(next_update_times.values())
                min_category = min(next_update_times, key=next_update_times.get)
                logger.info(f"Próxima atualização em {min_seconds}s (categoria: {min_category})")
                try:
                    scheduler.remove_job('stock_scraper')
                except:
                    pass
                scheduler.add_job(
                    scrape_stock,
                    'date',
                    run_date=datetime.now() + timedelta(seconds=min_seconds),
                    id='stock_scraper'
                )
            else:
                logger.warning("Não foi possível detectar tempos de atualização, usando 5 minutos")
                try:
                    scheduler.remove_job('stock_scraper')
                except:
                    pass
                scheduler.add_job(
                    scrape_stock,
                    'date',
                    run_date=datetime.now() + timedelta(minutes=5),
                    id='stock_scraper'
                )

            break  # Exit retry loop on success

        except requests.exceptions.HTTPError as e:
            if response.status_code == 403:
                logger.error(f"Erro 403 com o proxy: {proxies['http']}. Tentando outro proxy...")
            else:
                logger.error(f"Erro HTTP: {e}")
            continue
        except requests.RequestException as e:
            logger.error(f"Erro ao raspar com proxy {proxies['http']}: {e}")
            continue
        except Exception as e:
            logger.error(f"Erro inesperado: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            continue

    else:
        # After all retries, schedule a retry in 2 minutes
        logger.error(f"Falhou após {max_retries} tentativas. Reagendando em 2 minutos...")
        try:
            scheduler.remove_job('stock_scraper')
        except:
            pass
        scheduler.add_job(
            scrape_stock,
            'date',
            run_date=datetime.now() + timedelta(minutes=2),
            id='stock_scraper'
        )

# Configuração do agendador
scheduler = BackgroundScheduler()
scheduler.start()

# Inicializa o banco e faz o scraping inicial
init_db()
scrape_stock()

@app.route('/')
def home():
    """Página inicial com informações sobre a API."""
    return jsonify({
        'message': 'API de Estoque Grow a Garden',
        'endpoints': {
            '/api/grow-a-garden/stock': 'GET - Obter dados de estoque',
            '/api/grow-a-garden/stock?category=CATEGORIA': 'GET - Obter dados de uma categoria específica',
            '/api/grow-a-garden/stock/refresh': 'GET - Forçar atualização dos dados'
        },
        'categorias_disponíveis': ['seeds', 'gear', 'egg_shop', 'honey', 'cosmetics']
    })

@app.route('/api/grow-a-garden/stock', methods=['GET'])
def get_stock():
    """Retorna os dados de estoque."""
    category = request.args.get('category')
    if category:
        items, last_updated = load_from_db(category)
        if not items:
            return jsonify({'error': 'Categoria não encontrada ou sem dados'}), 404
        return jsonify({category: items, 'last_updated': last_updated})
    return jsonify(load_from_db())

@app.route('/api/grow-a-garden/stock/refresh', methods=['GET'])
def refresh_stock():
    """Força a atualização dos dados."""
    scrape_stock()
    return jsonify({'message': 'Dados atualizados', 'last_updated': load_from_db()['last_updated']})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)  # Desative o debug em produção
