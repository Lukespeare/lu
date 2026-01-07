import sqlite3
import os
import csv
import atexit
import requests
from datetime import datetime, date, timezone
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import random
from werkzeug.utils import secure_filename

# ==================== 系统配置 ====================
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "123456"
DATABASE_PATH = "database/restaurant.db"
ORDER_ID_PREFIX = "ORD"
TAKEOUT_FEE = 5.0
PRIVATE_ROOM_FEE = 20.0

# 图片上传配置
UPLOAD_FOLDER = "static/uploads"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB

# 订单导出配置
EXPORT_FOLDER = "order_exports"

# 网络时间配置
TIME_API_URL = "http://api.m.taobao.com/rest/api3.do?api=mtop.common.getTimestamp"
TIME_OUT_SECONDS = 3  # 网络请求超时时间

# ==================== 初始化配置 ====================
app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# 创建必要目录
os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)

# ==================== 工具函数 ====================
def allowed_file(filename):
    """检查文件是否为允许的图片格式"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_accurate_datetime():
    """获取准确的当前时间（优先网络时间，降级本地时间）"""
    try:
        response = requests.get(TIME_API_URL, timeout=TIME_OUT_SECONDS)
        response.raise_for_status()
        timestamp = int(response.json()["data"]["t"]) / 1000
        return datetime.fromtimestamp(timestamp)
    except Exception as e:
        print(f"获取网络时间失败({e})，使用本地时间")
        return datetime.now()

def get_accurate_time_str():
    """获取格式化的准确时间字符串（YYYY-MM-DD HH:MM:SS）"""
    return get_accurate_datetime().strftime("%Y-%m-%d %H:%M:%S")

def get_accurate_timestamp_str():
    """获取格式化的时间戳（用于订单编号）"""
    return get_accurate_datetime().strftime("%Y%m%d%H%M%S")

# ==================== 订单导出工具函数 ====================
def export_orders_to_file(date_str=None):
    """导出订单到CSV文件"""
    if not date_str:
        date_str = date.today().strftime("%Y-%m-%d")
    
    file_path = os.path.join(EXPORT_FOLDER, f"{date_str}_orders.csv")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT o.*, GROUP_CONCAT(d.name || ' x ' || oi.quantity || ' = ' || (oi.unit_price * oi.quantity)) as items
        FROM orders o
        LEFT JOIN order_items oi ON o.id = oi.order_id
        LEFT JOIN dishes d ON oi.dish_id = d.id
        WHERE DATE(o.create_time) = ?
        GROUP BY o.id
        ORDER BY o.create_time ASC
    """, (date_str,))
    orders = cursor.fetchall()
    conn.close()
    
    with open(file_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            '订单编号', '订单类型', '创建时间', '总金额(元)', 
            '餐桌号', '是否有包厢费', '包厢费(元)', '送餐时间', '送餐地址', 
            '手机号', '订单状态', '菜品明细'
        ])
        for order in orders:
            writer.writerow([
                order['order_no'],
                '到店' if order['order_type'] == 'dinein' else '外卖',
                order['create_time'],
                f"{order['total_amount']:.2f}",
                order['table_num'] or '',
                '是' if order['has_room_fee'] else '否',
                PRIVATE_ROOM_FEE if order['has_room_fee'] else 0.0,
                order['takeout_time'] or '',
                order['takeout_address'] or '',
                order['phone'],
                order['status'],
                order['items'] or ''
            ])
    return file_path

# ==================== 订单修改工具函数 ====================
def update_order_status(order_no, new_status):
    """修改订单状态"""
    valid_status = ['completed', 'cancelled', 'pending', 'delivered']
    if new_status not in valid_status:
        return False
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = ?, update_time = CURRENT_TIMESTAMP WHERE order_no = ?",
        (new_status, order_no)
    )
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0

def update_order_phone(order_no, new_phone):
    """修改订单手机号"""
    if len(new_phone) != 11 or not new_phone.isdigit():
        return False
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET phone = ?, update_time = CURRENT_TIMESTAMP WHERE order_no = ?",
        (new_phone, order_no)
    )
    conn.commit()
    affected = cursor.rowcount
    conn.close()
    return affected > 0

def update_order_field(order_no, field, value):
    """通用订单字段修改"""
    valid_fields = ['status', 'phone', 'table_num', 'takeout_address', 'takeout_time']
    if field not in valid_fields:
        return False
    
    if field == 'status' and value not in ['completed', 'cancelled', 'pending', 'delivered']:
        return False
    if field == 'phone' and (len(value) != 11 or not value.isdigit()):
        return False
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"UPDATE orders SET {field} = ?, update_time = CURRENT_TIMESTAMP WHERE order_no = ?",
            (value, order_no)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"修改订单字段失败：{e}")
        return False
    finally:
        conn.close()

# ==================== 订单项修改工具函数 ====================
def update_order_item(order_no, dish_id, new_quantity):
    """修改订单项数量"""
    if new_quantity <= 0:
        return False
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM orders WHERE order_no = ?", (order_no,))
        order_row = cursor.fetchone()
        if not order_row:
            return False
        order_id = order_row["id"]
        
        cursor.execute("SELECT price, discount FROM dishes WHERE id = ?", (dish_id,))
        dish_row = cursor.fetchone()
        if not dish_row:
            return False
        unit_price = round(dish_row["price"] * dish_row["discount"], 2)
        
        cursor.execute("""
            UPDATE order_items 
            SET quantity = ?, unit_price = ? 
            WHERE order_id = ? AND dish_id = ?
        """, (new_quantity, unit_price, order_id, dish_id))
        
        recalculate_order_total(order_id)
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"修改订单项失败：{e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def add_order_item(order_no, dish_id, quantity):
    """添加订单项"""
    if quantity <= 0:
        return False
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM orders WHERE order_no = ?", (order_no,))
        order_row = cursor.fetchone()
        if not order_row:
            return False
        order_id = order_row["id"]
        
        cursor.execute("SELECT name, price, discount FROM dishes WHERE id = ?", (dish_id,))
        dish_row = cursor.fetchone()
        if not dish_row:
            return False
        unit_price = round(dish_row["price"] * dish_row["discount"], 2)
        
        cursor.execute("""
            SELECT id FROM order_items 
            WHERE order_id = ? AND dish_id = ?
        """, (order_id, dish_id))
        if cursor.fetchone():
            return False
        
        cursor.execute("""
            INSERT INTO order_items (order_id, dish_id, quantity, unit_price)
            VALUES (?, ?, ?, ?)
        """, (order_id, dish_id, quantity, unit_price))
        
        recalculate_order_total(order_id)
        conn.commit()
        return True
    except Exception as e:
        print(f"添加订单项失败：{e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def delete_order_item(order_no, dish_id):
    """删除订单项"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM orders WHERE order_no = ?", (order_no,))
        order_row = cursor.fetchone()
        if not order_row:
            return False
        order_id = order_row["id"]
        
        cursor.execute("""
            DELETE FROM order_items 
            WHERE order_id = ? AND dish_id = ?
        """, (order_id, dish_id))
        
        recalculate_order_total(order_id)
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"删除订单项失败：{e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def recalculate_order_total(order_id):
    """重新计算订单总金额"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT order_type, has_room_fee FROM orders WHERE id = ?", (order_id,))
        order_row = cursor.fetchone()
        if not order_row:
            return
        
        cursor.execute("""
            SELECT SUM(quantity * unit_price) as dish_total 
            FROM order_items 
            WHERE order_id = ?
        """, (order_id,))
        dish_total = cursor.fetchone()["dish_total"] or 0.0
        
        additional_fee = 0.0
        if order_row["order_type"] == "dinein" and order_row["has_room_fee"]:
            additional_fee = PRIVATE_ROOM_FEE
        elif order_row["order_type"] == "takeout":
            additional_fee = TAKEOUT_FEE
        
        total_amount = round(dish_total + additional_fee, 2)
        cursor.execute("""
            UPDATE orders 
            SET total_amount = ? 
            WHERE id = ?
        """, (total_amount, order_id))
        conn.commit()
    except Exception as e:
        print(f"重新计算订单金额失败：{e}")
        conn.rollback()
    finally:
        conn.close()

# ==================== 数据库初始化 ====================
def init_database():
    """初始化数据库表结构"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. 菜品表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS dishes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        price REAL NOT NULL CHECK(price > 0),
        discount REAL DEFAULT 1.0 CHECK(discount > 0 AND discount <= 1.0),
        dish_image TEXT,
        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 2. 订单表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT UNIQUE NOT NULL,
        order_type TEXT NOT NULL CHECK(order_type IN ('dinein', 'takeout')),
        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        total_amount REAL NOT NULL CHECK(total_amount >= 0),
        table_num TEXT,
        has_room_fee INTEGER DEFAULT 0 CHECK(has_room_fee IN (0, 1)),
        takeout_time TEXT,
        takeout_address TEXT,
        phone TEXT NOT NULL,
        status TEXT DEFAULT "completed" CHECK(status IN ('completed', 'cancelled', 'pending', 'delivered'))
    )
    ''')
    
    # 3. 订单项表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        dish_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity > 0),
        unit_price REAL NOT NULL CHECK(unit_price >= 0),
        FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
        FOREIGN KEY (dish_id) REFERENCES dishes(id) ON DELETE CASCADE
    )
    ''')
    
    # 4. 管理员表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )
    ''')
    
    # 初始化默认管理员
    cursor.execute('SELECT * FROM admins WHERE username = ?', (ADMIN_USERNAME,))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO admins (username, password) VALUES (?, ?)', 
                      (ADMIN_USERNAME, ADMIN_PASSWORD))
    
    conn.commit()
    conn.close()
    print("✅ 数据库初始化完成")

# ==================== 核心类定义 ====================
class Dish:
    """菜品类"""
    def __init__(self, name, price, discount=1.0, dish_id=None, dish_image=None):
        self.id = dish_id
        self.name = name
        self.price = price
        self.discount = discount
        self.dish_image = dish_image
        self._final_price = round(self.price * self.discount, 2)

    @property
    def final_price(self):
        """获取折后价（只读）"""
        return self._final_price

    def update_price(self, new_price):
        """更新价格并重新计算折后价"""
        if new_price <= 0:
            raise ValueError("价格必须大于0")
        self.price = new_price
        self._final_price = round(new_price * self.discount, 2)

    def update_discount(self, new_discount):
        """更新折扣并重新计算折后价"""
        if not (0 < new_discount <= 1.0):
            raise ValueError("折扣必须在0-1之间")
        self.discount = new_discount
        self._final_price = round(self.price * new_discount, 2)

    def __str__(self):
        return f"菜品：{self.name} | 原价：{self.price:.2f} | 折扣：{self.discount*100:.0f}% | 折后价：{self.final_price:.2f}"

class Order:
    """订单基类"""
    def __init__(self, order_type, phone):
        self.order_type = order_type
        self.phone = phone
        self.order_no = self._generate_order_no()
        self.create_time = get_accurate_time_str()
        self.items = []
        self.total_amount = 0.0
        self.status = "completed"

    def _generate_order_no(self):
        """生成唯一订单编号"""
        timestamp = get_accurate_timestamp_str()
        random_suffix = random.randint(100, 999)
        return f"{ORDER_ID_PREFIX}{timestamp}{random_suffix}"

    def add_item(self, dish, quantity):
        """添加菜品到订单"""
        if quantity <= 0:
            raise ValueError("数量必须大于0")
        self.items.append((dish, quantity))
        self._calculate_total()

    def _calculate_total(self):
        """计算总金额（子类实现）"""
        raise NotImplementedError

    def get_order_info(self):
        """获取订单信息（子类实现）"""
        raise NotImplementedError

class DineInOrder(Order):
    """到店订单"""
    def __init__(self, table_num, phone, has_room_fee=False):
        super().__init__("dinein", phone)
        self.table_num = table_num
        self.has_room_fee = has_room_fee
        self.room_fee = PRIVATE_ROOM_FEE if has_room_fee else 0.0

    def _calculate_total(self):
        """计算到店订单总金额"""
        dish_total = sum([dish.final_price * quantity for dish, quantity in self.items])
        self.total_amount = round(dish_total + self.room_fee, 2)

    def get_order_info(self):
        """获取到店订单详情"""
        if not self.items:
            item_info = "无菜品"
        else:
            item_info = "\n  - ".join([f"{dish.name} x {quantity} = {dish.final_price * quantity:.2f}" 
                                       for dish, quantity in self.items])
        
        return f"""
===== 到店订单 =====
订单编号：{self.order_no}
下单时间：{self.create_time}
餐桌号：{self.table_num}
手机号：{self.phone}
是否有包厢费：{"是" if self.has_room_fee else "否"}
包厢费：{self.room_fee:.2f}元
菜品明细：
  - {item_info}
订单总金额：{self.total_amount:.2f}元
订单状态：{self.status}
===================
        """

class TakeoutOrder(Order):
    """外卖订单"""
    def __init__(self, takeout_time, takeout_address, phone):
        super().__init__("takeout", phone)
        self.takeout_time = takeout_time
        self.takeout_address = takeout_address
        self.takeout_fee = TAKEOUT_FEE

    def _calculate_total(self):
        """计算外卖订单总金额"""
        dish_total = sum([dish.final_price * quantity for dish, quantity in self.items])
        self.total_amount = round(dish_total + self.takeout_fee, 2)

    def get_order_info(self):
        """获取外卖订单详情"""
        if not self.items:
            item_info = "无菜品"
        else:
            item_info = "\n  - ".join([f"{dish.name} x {quantity} = {dish.final_price * quantity:.2f}" 
                                       for dish, quantity in self.items])
        
        return f"""
===== 外卖订单 =====
订单编号：{self.order_no}
下单时间：{self.create_time}
送餐时间：{self.takeout_time}
送餐地址：{self.takeout_address}
手机号：{self.phone}
外卖服务费：{self.takeout_fee:.2f}元
菜品明细：
  - {item_info}
订单总金额：{self.total_amount:.2f}元
订单状态：{self.status}
===================
        """

# ==================== 菜品管理类 ====================
class DishManager:
    """菜品管理类"""
    @staticmethod
    def add_dish(name, price, discount=1.0, dish_image=None):
        """添加新菜品"""
        if price <= 0 or not (0 < discount <= 1.0):
            return False
            
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO dishes (name, price, discount, dish_image) VALUES (?, ?, ?, ?)",
                (name.strip(), price, discount, dish_image)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    @staticmethod
    def get_all_dishes():
        """获取所有菜品"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dishes ORDER BY id")
        rows = cursor.fetchall()
        conn.close()
        
        dishes = []
        for row in rows:
            dish = Dish(
                name=row["name"],
                price=row["price"],
                discount=row["discount"],
                dish_id=row["id"],
                dish_image=row["dish_image"]
            )
            dishes.append(dish)
        return dishes

    @staticmethod
    def get_dish_by_id(dish_id):
        """通过ID获取菜品"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return Dish(
                name=row["name"],
                price=row["price"],
                discount=row["discount"],
                dish_id=row["id"],
                dish_image=row["dish_image"]
            )
        return None

    @staticmethod
    def get_dish_by_name(name):
        """通过名称获取菜品"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dishes WHERE name = ?", (name.strip(),))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return Dish(
                name=row["name"],
                price=row["price"],
                discount=row["discount"],
                dish_id=row["id"],
                dish_image=row["dish_image"]
            )
        return None

    @staticmethod
    def update_dish(dish_id, new_name=None, new_price=None, new_discount=None, new_image=None):
        """更新菜品信息"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,))
        if not cursor.fetchone():
            conn.close()
            return False
        
        update_fields = []
        params = []
        
        if new_name:
            update_fields.append("name = ?")
            params.append(new_name.strip())
        if new_price is not None and new_price > 0:
            update_fields.append("price = ?")
            params.append(new_price)
        if new_discount is not None and 0 < new_discount <= 1.0:
            update_fields.append("discount = ?")
            params.append(new_discount)
        if new_image is not None:
            update_fields.append("dish_image = ?")
            params.append(new_image)
        
        if not update_fields:
            conn.close()
            return True
        
        update_fields.append("update_time = CURRENT_TIMESTAMP")
        sql = f"UPDATE dishes SET {', '.join(update_fields)} WHERE id = ?"
        params.append(dish_id)
        
        cursor.execute(sql, params)
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def delete_dish(dish_id):
        """删除菜品"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM dishes WHERE id = ?", (dish_id,))
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return affected > 0

# ==================== 订单管理类 ====================
class OrderManager:
    """订单管理类"""
    @staticmethod
    def save_order(order):
        """保存订单到数据库"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            if isinstance(order, DineInOrder):
                cursor.execute(
                    """INSERT INTO orders 
                    (order_no, order_type, create_time, total_amount, table_num, has_room_fee, phone, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (order.order_no, order.order_type, order.create_time, order.total_amount,
                     order.table_num, 1 if order.has_room_fee else 0, order.phone, order.status)
                )
            elif isinstance(order, TakeoutOrder):
                cursor.execute(
                    """INSERT INTO orders 
                    (order_no, order_type, create_time, total_amount, takeout_time, takeout_address, phone, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (order.order_no, order.order_type, order.create_time, order.total_amount,
                     order.takeout_time, order.takeout_address, order.phone, order.status)
                )
            
            order_id = cursor.lastrowid
            
            for dish, quantity in order.items:
                cursor.execute(
                    """INSERT INTO order_items 
                    (order_id, dish_id, quantity, unit_price)
                    VALUES (?, ?, ?, ?)""",
                    (order_id, dish.id, quantity, dish.final_price)
                )
            
            conn.commit()
            return True
        except Exception as e:
            print(f"保存订单失败：{e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    @staticmethod
    def get_order_by_no(order_no):
        """通过订单编号获取订单"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,))
        order_row = cursor.fetchone()
        if not order_row:
            conn.close()
            return None
        
        cursor.execute("""
            SELECT oi.*, d.name, d.price, d.discount, d.dish_image 
            FROM order_items oi
            JOIN dishes d ON oi.dish_id = d.id
            WHERE oi.order_id = ?
        """, (order_row["id"],))
        item_rows = cursor.fetchall()
        conn.close()

        if order_row["order_type"] == "dinein":
            order = DineInOrder(
                table_num=order_row["table_num"],
                phone=order_row["phone"],
                has_room_fee=bool(order_row["has_room_fee"])
            )
        else:
            order = TakeoutOrder(
                takeout_time=order_row["takeout_time"],
                takeout_address=order_row["takeout_address"],
                phone=order_row["phone"]
            )
        
        order.order_no = order_row["order_no"]
        order.create_time = order_row["create_time"]
        order.total_amount = order_row["total_amount"]
        order.status = order_row["status"]
        
        for item in item_rows:
            dish = Dish(
                name=item["name"],
                price=item["price"],
                discount=item["discount"],
                dish_id=item["dish_id"],
                dish_image=item["dish_image"]
            )
            order.add_item(dish, item["quantity"])
        
        return order

    @staticmethod
    def get_orders_by_phone(phone):
        """通过手机号获取所有订单"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT order_no FROM orders 
            WHERE phone = ? 
            ORDER BY create_time DESC
        """, (phone,))
        order_rows = cursor.fetchall()
        conn.close()
        
        orders = []
        for row in order_rows:
            order = OrderManager.get_order_by_no(row["order_no"])
            if order:
                orders.append(order)
        return orders

    @staticmethod
    def get_orders_by_date(date_str):
        """获取指定日期的所有订单"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT order_no FROM orders 
            WHERE DATE(create_time) = ? 
            ORDER BY create_time
        """, (date_str,))
        order_rows = cursor.fetchall()
        conn.close()
        
        orders = []
        for row in order_rows:
            order = OrderManager.get_order_by_no(row["order_no"])
            if order:
                orders.append(order)
        return orders

    @staticmethod
    def delete_order(order_no):
        """删除订单"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM orders WHERE order_no = ?", (order_no,))
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return affected > 0

    @staticmethod
    def get_order_items(order_no):
        """获取订单的菜品明细"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM orders WHERE order_no = ?", (order_no,))
            order_row = cursor.fetchone()
            if not order_row:
                return []
            
            cursor.execute("""
                SELECT oi.dish_id, d.name, oi.quantity, oi.unit_price 
                FROM order_items oi
                JOIN dishes d ON oi.dish_id = d.id
                WHERE oi.order_id = ?
            """, (order_row["id"],))
            items = cursor.fetchall()
            return [
                {
                    "dish_id": item["dish_id"],
                    "name": item["name"],
                    "quantity": item["quantity"],
                    "unit_price": item["unit_price"]
                } for item in items
            ]
        finally:
            conn.close()

    @staticmethod
    def get_all_dishes_for_order():
        """获取所有可添加的菜品"""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, discount FROM dishes ORDER BY name")
        dishes = cursor.fetchall()
        conn.close()
        return [
            {
                "dish_id": dish["id"],
                "name": dish["name"],
                "final_price": round(dish["price"] * dish["discount"], 2)
            } for dish in dishes
        ]

    @staticmethod
    def get_sales_stats(date_str):
        """获取指定日期的销售统计"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_orders,
                SUM(total_amount) as total_sales,
                SUM(CASE WHEN order_type = 'takeout' THEN 1 ELSE 0 END) as takeout_count,
                SUM(CASE WHEN order_type = 'dinein' THEN 1 ELSE 0 END) as dinein_count,
                SUM(CASE WHEN order_type = 'takeout' THEN total_amount ELSE 0 END) as takeout_sales,
                SUM(CASE WHEN order_type = 'dinein' THEN total_amount ELSE 0 END) as dinein_sales
            FROM orders 
            WHERE DATE(create_time) = ?
        """, (date_str,))
        
        stats_row = cursor.fetchone()
        if not stats_row or stats_row['total_orders'] == 0:
            conn.close()
            return None
        
        cursor.execute("""
            SELECT 
                d.name,
                SUM(oi.quantity) as quantity,
                SUM(oi.unit_price * oi.quantity) as amount
            FROM order_items oi
            LEFT JOIN dishes d ON oi.dish_id = d.id
            LEFT JOIN orders o ON oi.order_id = o.id
            WHERE DATE(o.create_time) = ?
            GROUP BY d.name
            HAVING d.name IS NOT NULL
            ORDER BY amount DESC
        """, (date_str,))
        
        dish_rows = cursor.fetchall()
        dish_stats = {}
        for row in dish_rows:
            dish_stats[row['name']] = {
                'quantity': row['quantity'],
                'amount': row['amount']
            }
        
        conn.close()
        
        total_orders = stats_row['total_orders']
        total_sales = stats_row['total_sales'] or 0
        takeout_count = stats_row['takeout_count'] or 0
        dinein_count = stats_row['dinein_count'] or 0
        takeout_sales = stats_row['takeout_sales'] or 0
        dinein_sales = stats_row['dinein_sales'] or 0
        
        takeout_ratio = (takeout_count / total_orders * 100) if total_orders > 0 else 0
        dinein_ratio = (dinein_count / total_orders * 100) if total_orders > 0 else 0
        takeout_sales_ratio = (takeout_sales / total_sales * 100) if total_sales > 0 else 0
        dinein_sales_ratio = (dinein_sales / total_sales * 100) if total_sales > 0 else 0
        
        return {
            "date": date_str,
            "total_orders": total_orders,
            "total_sales": round(total_sales, 2),
            "dish_stats": dish_stats,
            "takeout": {
                "count": takeout_count,
                "sales": round(takeout_sales, 2),
                "ratio": round(takeout_ratio, 1),
                "sales_ratio": round(takeout_sales_ratio, 1)
            },
            "dinein": {
                "count": dinein_count,
                "sales": round(dinein_sales, 2),
                "ratio": round(dinein_ratio, 1),
                "sales_ratio": round(dinein_sales_ratio, 1)
            }
        }

# ==================== Flask路由 ====================
# 首页
@app.route('/')
def index():
    """顾客点餐首页"""
    dishes = DishManager.get_all_dishes()
    return render_template('index.html', dishes=dishes)

# 获取所有菜品（供订单修改）
@app.route('/get_all_dishes')
def get_all_dishes():
    """获取所有可添加的菜品"""
    dishes = OrderManager.get_all_dishes_for_order()
    return jsonify(dishes)

# 订单查询（支持AJAX和修改）
@app.route('/query_order', methods=['GET', 'POST'])
def query_order():
    """顾客订单查询页面（支持修改）"""
    if request.method == 'POST':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            search_type = request.form.get('search_type', '')
            keyword = request.form.get('keyword', '').strip()
            
            if not keyword:
                return jsonify({"success": False, "error": "请输入查询关键词"})
            
            orders = []
            if search_type == 'order_no':
                order = OrderManager.get_order_by_no(keyword)
                if order:
                    items = OrderManager.get_order_items(keyword)
                    orders.append({
                        "order_no": order.order_no,
                        "order_type": order.order_type,
                        "create_time": order.create_time,
                        "total_amount": order.total_amount,
                        "table_num": getattr(order, 'table_num', ''),
                        "has_room_fee": getattr(order, 'has_room_fee', False),
                        "takeout_time": getattr(order, 'takeout_time', ''),
                        "takeout_address": getattr(order, 'takeout_address', ''),
                        "phone": order.phone,
                        "status": order.status,
                        "items": items
                    })
            elif search_type == 'phone':
                if len(keyword) != 11 or not keyword.isdigit():
                    return jsonify({"success": False, "error": "请输入有效的11位手机号"})
                order_list = OrderManager.get_orders_by_phone(keyword)
                for order in order_list:
                    items = OrderManager.get_order_items(order.order_no)
                    orders.append({
                        "order_no": order.order_no,
                        "order_type": order.order_type,
                        "create_time": order.create_time,
                        "total_amount": order.total_amount,
                        "table_num": getattr(order, 'table_num', ''),
                        "has_room_fee": getattr(order, 'has_room_fee', False),
                        "takeout_time": getattr(order, 'takeout_time', ''),
                        "takeout_address": getattr(order, 'takeout_address', ''),
                        "phone": order.phone,
                        "status": order.status,
                        "items": items
                    })
            else:
                return jsonify({"success": False, "error": "无效的查询类型"})
            
            return jsonify({
                "success": len(orders) > 0,
                "orders": orders,
                "error": "未找到相关订单" if len(orders) == 0 else ""
            })
        else:
            search_type = request.form.get('search_type', '')
            keyword = request.form.get('keyword', '').strip()
            order_info = None
            error = None
            
            if not keyword:
                error = "请输入查询关键词"
            else:
                if search_type == 'order_no':
                    order = OrderManager.get_order_by_no(keyword)
                    if order:
                        order_info = order.get_order_info()
                    else:
                        error = "未找到该订单"
                elif search_type == 'phone':
                    if len(keyword) != 11 or not keyword.isdigit():
                        error = "请输入有效的11位手机号"
                    else:
                        orders = OrderManager.get_orders_by_phone(keyword)
                        if orders:
                            order_info = "\n\n".join([o.get_order_info() for o in orders])
                        else:
                            error = "未找到该手机号的订单"
                else:
                    error = "无效的查询类型"
            
            return render_template('query_order.html', order_info=order_info, error=error)
    return render_template('query_order.html')

# 订单修改接口
@app.route('/update_order', methods=['POST'])
def update_order():
    """通用订单修改接口"""
    order_no = request.form.get('order_no', '').strip()
    field = request.form.get('field', '').strip()
    new_value = request.form.get('new_value', '').strip()
    
    if not order_no or not field or not new_value:
        return jsonify({"success": False, "error": "参数不完整"})
    
    success = update_order_field(order_no, field, new_value)
    if success:
        return jsonify({"success": True, "message": "订单修改成功"})
    else:
        return jsonify({"success": False, "error": "修改失败（订单不存在或参数无效）"})

# 订单项修改接口
@app.route('/order/update_item', methods=['POST'])
def update_order_item_api():
    """修改订单菜品数量"""
    order_no = request.form.get('order_no', '').strip()
    dish_id = request.form.get('dish_id', '').strip()
    new_quantity = request.form.get('new_quantity', '').strip()
    
    try:
        dish_id = int(dish_id)
        new_quantity = int(new_quantity)
    except ValueError:
        return jsonify({"success": False, "error": "参数格式错误"})
    
    success = update_order_item(order_no, dish_id, new_quantity)
    if success:
        return jsonify({"success": True, "message": "菜品数量修改成功"})
    else:
        return jsonify({"success": False, "error": "修改失败（订单/菜品不存在或数量无效）"})

@app.route('/order/add_item', methods=['POST'])
def add_order_item_api():
    """给订单添加新菜品"""
    order_no = request.form.get('order_no', '').strip()
    dish_id = request.form.get('dish_id', '').strip()
    quantity = request.form.get('quantity', '').strip()
    
    try:
        dish_id = int(dish_id)
        quantity = int(quantity)
    except ValueError:
        return jsonify({"success": False, "error": "参数格式错误"})
    
    success = add_order_item(order_no, dish_id, quantity)
    if success:
        return jsonify({"success": True, "message": "菜品添加成功"})
    else:
        return jsonify({"success": False, "error": "添加失败（订单/菜品不存在或菜品已在订单中）"})

@app.route('/order/delete_item', methods=['POST'])
def delete_order_item_api():
    """删除订单中的菜品"""
    order_no = request.form.get('order_no', '').strip()
    dish_id = request.form.get('dish_id', '').strip()
    
    try:
        dish_id = int(dish_id)
    except ValueError:
        return jsonify({"success": False, "error": "参数格式错误"})
    
    success = delete_order_item(order_no, dish_id)
    if success:
        return jsonify({"success": True, "message": "菜品删除成功"})
    else:
        return jsonify({"success": False, "error": "删除失败（订单/菜品不存在）"})

# 提交订单
@app.route('/submit_order', methods=['POST'])
def submit_order():
    """提交订单接口"""
    try:
        order_type = request.form.get('order_type', '')
        phone = request.form.get('phone', '').strip()
        
        if len(phone) != 11 or not phone.isdigit():
            return jsonify({
                "success": False, 
                "error": "请输入有效的11位手机号"
            })
        
        if order_type == 'dinein':
            table_num = request.form.get('table_num', '').strip()
            if not table_num:
                return jsonify({"success": False, "error": "请输入餐桌号"})
            
            has_room_fee = request.form.get('has_room_fee') == '1'
            order = DineInOrder(table_num=table_num, phone=phone, has_room_fee=has_room_fee)
        
        elif order_type == 'takeout':
            takeout_time = request.form.get('takeout_time', '').strip()
            takeout_address = request.form.get('takeout_address', '').strip()
            
            if not takeout_time:
                return jsonify({"success": False, "error": "请输入送餐时间"})
            if not takeout_address:
                return jsonify({"success": False, "error": "请输入送餐地址"})
            
            order = TakeoutOrder(
                takeout_time=takeout_time,
                takeout_address=takeout_address,
                phone=phone
            )
        
        else:
            return jsonify({"success": False, "error": "无效的订单类型"})
        
        dish_ids = request.form.getlist('dish_id[]')
        quantities = request.form.getlist('quantity[]')
        has_items = False
        
        for dish_id, quantity in zip(dish_ids, quantities):
            try:
                dish_id = int(dish_id)
                quantity = int(quantity)
                
                if quantity > 0:
                    dish = DishManager.get_dish_by_id(dish_id)
                    if dish:
                        order.add_item(dish, quantity)
                        has_items = True
            except (ValueError, TypeError):
                continue
        
        if not has_items:
            return jsonify({"success": False, "error": "请选择至少一道菜品"})
        
        if OrderManager.save_order(order):
            return jsonify({
                "success": True, 
                "order_info": order.get_order_info(),
                "order_no": order.order_no
            })
        else:
            return jsonify({"success": False, "error": "保存订单失败，请重试"})
    
    except Exception as e:
        return jsonify({
            "success": False, 
            "error": f"系统错误：{str(e)}"
        })

# ==================== 管理员路由 ====================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """管理员登录"""
    if session.get('admin'):
        return redirect(url_for('admin_panel'))
    
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_panel'))
        else:
            error = "账号或密码错误"
    
    return render_template('admin_login.html', error=error)

@app.route('/admin/panel')
def admin_panel():
    """管理员面板"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    dishes = DishManager.get_all_dishes()
    return render_template('admin_panel.html', dishes=dishes)

@app.route('/admin/logout')
def admin_logout():
    """管理员退出登录"""
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

# ==================== 菜品管理接口 ====================
@app.route('/admin/dish/add', methods=['POST'])
def add_dish():
    """添加菜品接口"""
    if not session.get('admin'):
        return jsonify({"success": False, "error": "未登录"})
    
    try:
        name = request.form.get('name', '').strip()
        price = float(request.form.get('price', 0))
        discount = float(request.form.get('discount', 1.0))
        
        if not name:
            return jsonify({"success": False, "error": "请输入菜品名称"})
        
        dish_image = None
        if 'dish_image' in request.files:
            file = request.files['dish_image']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{get_accurate_timestamp_str()}_{filename}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(file_path)
                dish_image = f"/static/uploads/{unique_filename}"
        
        if DishManager.add_dish(name, price, discount, dish_image):
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "菜品名称已存在或参数无效"})
    
    except Exception as e:
        return jsonify({"success": False, "error": f"服务器错误：{str(e)}"})

@app.route('/admin/dish/update', methods=['POST'])
def update_dish():
    """修改菜品接口"""
    if not session.get('admin'):
        return jsonify({"success": False, "error": "未登录"})
    
    try:
        dish_id = int(request.form.get('dish_id', 0))
        new_name = request.form.get('new_name', '').strip()
        new_price = request.form.get('new_price', '')
        new_discount = request.form.get('new_discount', '')
        
        new_price = float(new_price) if new_price else None
        new_discount = float(new_discount) if new_discount else None
        
        new_image = None
        if 'new_image' in request.files:
            file = request.files['new_image']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{get_accurate_timestamp_str()}_{filename}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(file_path)
                new_image = f"/static/uploads/{unique_filename}"
        
        if DishManager.update_dish(dish_id, new_name, new_price, new_discount, new_image):
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "菜品不存在或参数无效"})
    
    except Exception as e:
        return jsonify({"success": False, "error": f"服务器错误：{str(e)}"})

@app.route('/admin/dish/delete', methods=['POST'])
def delete_dish():
    """删除菜品接口"""
    if not session.get('admin'):
        return jsonify({"success": False, "error": "未登录"})
    
    try:
        dish_id = int(request.form.get('dish_id', 0))
        if DishManager.delete_dish(dish_id):
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "菜品不存在"})
    except Exception as e:
        return jsonify({"success": False, "error": f"服务器错误：{str(e)}"})

# ==================== 管理员订单管理接口 ====================
@app.route('/admin/order/search', methods=['POST'])
def admin_search_order():
    """管理员搜索订单"""
    if not session.get('admin'):
        return jsonify({"success": False, "error": "未登录"})
    
    search_type = request.form.get('search_type', '')
    keyword = request.form.get('keyword', '').strip()
    
    if not keyword:
        return jsonify({"success": False, "error": "请输入搜索关键词"})
    
    try:
        if search_type == 'order_no':
            order = OrderManager.get_order_by_no(keyword)
            orders = [order] if order else []
        elif search_type == 'phone':
            orders = OrderManager.get_orders_by_phone(keyword)
        else:
            return jsonify({"success": False, "error": "无效的搜索类型"})
        
        order_infos = []
        for o in orders:
            order_infos.append({
                "order_no": o.order_no,
                "info": o.get_order_info(),
                "type": o.order_type
            })
        
        return jsonify({"success": True, "orders": order_infos})
    
    except Exception as e:
        return jsonify({"success": False, "error": f"服务器错误：{str(e)}"})

@app.route('/admin/order/delete', methods=['POST'])
def admin_delete_order():
    """管理员删除订单"""
    if not session.get('admin'):
        return jsonify({"success": False, "error": "未登录"})
    
    order_no = request.form.get('order_no', '').strip()
    if not order_no:
        return jsonify({"success": False, "error": "请输入订单编号"})
    
    if OrderManager.delete_order(order_no):
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "订单不存在"})

@app.route('/admin/order/update', methods=['POST'])
def admin_update_order():
    """管理员修改订单"""
    if not session.get('admin'):
        return jsonify({"success": False, "error": "未登录"})
    
    try:
        order_no = request.form.get('order_no', '').strip()
        update_type = request.form.get('update_type', '')
        new_value = request.form.get('new_value', '').strip()
        
        if not order_no or not update_type or not new_value:
            return jsonify({"success": False, "error": "参数不完整"})
        
        success = update_order_field(order_no, update_type, new_value)
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "订单不存在或参数无效"})
    
    except Exception as e:
        return jsonify({"success": False, "error": f"服务器错误：{str(e)}"})

@app.route('/admin/export_orders', methods=['POST'])
def admin_export_orders():
    """管理员导出订单"""
    if not session.get('admin'):
        return jsonify({"success": False, "error": "未登录"})
    
    try:
        date_str = request.form.get('date', date.today().strftime("%Y-%m-%d"))
        file_path = export_orders_to_file(date_str)
        return jsonify({
            "success": True, 
            "message": f"订单已成功导出到：{os.path.abspath(file_path)}"
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"导出失败：{str(e)}"})

# ==================== 销售统计 ====================
@app.route('/admin/sales', methods=['GET', 'POST'])
def sales_stats():
    """销售统计页面"""
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    today = date.today().strftime("%Y-%m-%d")
    date_str = request.form.get('date', today) if request.method == 'POST' else today
    stats = OrderManager.get_sales_stats(date_str)
    
    return render_template('sales_stats.html', 
                           stats=stats, 
                           date_str=date_str,
                           today=today)

# ==================== 退出时自动导出订单 ====================
@atexit.register
def on_exit():
    """程序退出时自动导出当天订单"""
    try:
        export_orders_to_file()
        print(f"\n✅ 当天订单已自动导出到 {EXPORT_FOLDER} 目录")
    except Exception as e:
        print(f"\n❌ 退出时导出订单失败：{str(e)}")

# ==================== 启动应用 ====================
if __name__ == '__main__':
    init_database()
    
    print("\n🚀 餐厅点餐系统启动成功！")
    print("🌐 访问地址：http://127.0.0.1:5000")
    print("🔑 管理员账号：admin / 123456")
    print("💡 系统使用网络时间校准，确保订单时间准确")
    print("💡 顾客可通过订单号/手机号查询并修改订单")
    print("💡 退出系统时会自动导出当天订单到 order_exports 目录")
    print("="*50)
    
    app.run(
        host='0.0.0.0', 
        port=5000, 
        debug=True,
        use_reloader=False
    )