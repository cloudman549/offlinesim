from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime
import os
from bson.objectid import ObjectId
import io
import requests
import json

app = Flask(__name__)
app.secret_key = 'customer-secret-key-change-this'
CORS(app)

# MongoDB Connection
client = MongoClient("mongodb+srv://salamuddinsah133_db_user:YzPdoa1LFYEyvtr4@cluster0.vjgqrip.mongodb.net/?appName=Cluster0")
db = client['offlineSim']

# Collections
config_collection = db['config']
orders_collection = db['orders']
normal_ids_collection = db['normal_ids']
verified_ids_collection = db['verified_ids']
used_utrs_collection = db['used_utrs']
users_collection = db['users']
service_prices_collection = db['service_prices']

# Initialize default config
def init_config():
    if config_collection.count_documents({}) == 0:
        default_config = {
            'normal_price': 200,
            'verified_price': 400,
            'qr_code': '',
            'announcement': '',
            'bharatpe_token': '',
            'bharatpe_mid': '',
            'upi_id': '',
            'whatsapp_link': 'https://wa.link/soyd4g',
            'telegram_link': 'https://l1nq.link/?telegram=SUPERMAN_ADMIN1',
            'vip_utrs': []
        }
        config_collection.insert_one(default_config)

def is_utr_used(utr):
    """Check if UTR already used in used_utrs"""
    if used_utrs_collection.find_one({'utr': utr}):
        return True
    return False

def is_vip_utr(utr):
    """Check if UTR is in VIP list (manual approval required)"""
    config = config_collection.find_one({})
    vip_utrs = config.get('vip_utrs', [])
    
    for vip_utr in vip_utrs:
        if vip_utr.get('utr') == utr and vip_utr.get('active', True):
            return True
    return False

def verify_payment_bharatpe(utr, expected_amount):
    """
    Verify payment using BharatPe API
    Returns: (success, message, actual_amount)
    """
    try:
        config = config_collection.find_one({})
        token = config.get('bharatpe_token', '')
        mid = config.get('bharatpe_mid', '')
        
        if not token or not mid:
            return False, 'Payment gateway not configured. Please contact admin.', 0
        
        url = f"https://payments-tesseract.bharatpe.in/api/v1/merchant/transactions?module=PAYMENT_QR&merchantId={mid}"
        
        headers = {"token": token}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return False, f'Payment gateway error: {response.status_code}', 0
        
        data = response.json()
        
        if 'data' not in data or 'transactions' not in data['data']:
            return False, 'Invalid API response', 0
        
        transactions = data['data']['transactions']
        
        for transaction in transactions:
            bank_ref = transaction.get('bankReferenceNo', '')
            
            if bank_ref == utr:
                amount = float(transaction.get('amount', 0))
                
                if amount >= expected_amount:
                    return True, 'Payment verified successfully', amount
                else:
                    return False, f'Invalid amount. Expected ₹{expected_amount}, got ₹{amount}', amount
        
        return False, 'Payment not found', 0
        
    except requests.RequestException as e:
        print(f"❌ BharatPe API Error: {str(e)}")
        return False, f'Payment verification failed: Connection error', 0
    except Exception as e:
        print(f"❌ Payment Verification Error: {str(e)}")
        return False, f'Payment verification failed: {str(e)}', 0

def allocate_ids(id_type, quantity):
    """
    Allocate IDs from database and mark as sold
    Returns: list of IDs or None if not enough stock
    """
    collection = normal_ids_collection if id_type == 'normal' else verified_ids_collection
    
    available_ids = list(collection.find({'sold': False}).limit(quantity))
    
    if len(available_ids) < quantity:
        return None
    
    delivered_ids = []
    
    for id_doc in available_ids:
        collection.update_one(
            {'_id': id_doc['_id']},
            {'$set': {'sold': True, 'sold_date': datetime.now()}}
        )
        
        delivered_ids.append({
            'userid': id_doc.get('userid', ''),
            'password': id_doc.get('password', ''),
            'email': id_doc.get('email', ''),
            'email_password': id_doc.get('email_password', '')
        })
    
    return delivered_ids

# Initialize
init_config()

@app.route('/')
def index():
    return render_template('index.html')

# ← SIRF YE 4 LINES ADD KI HAIN
@app.route('/virtual-number')
@app.route('/virtual-number.html')
def virtual_number():
    return render_template('virtual-number.html')

@app.route('/buynumber')
@app.route('/buynumber.html')
def buy_number_page():
    return render_template('buynumber.html')

@app.route('/api/config')
def get_config():
    config = config_collection.find_one({}, {'_id': 0})
    
    normal_available = normal_ids_collection.count_documents({'sold': False})
    verified_available = verified_ids_collection.count_documents({'sold': False})
    
    return jsonify({
        'normal_price': config.get('normal_price', 200),
        'verified_price': config.get('verified_price', 400),
        'qr_code': config.get('qr_code', ''),
        'announcement': config.get('announcement', ''),
        'upi_id': config.get('upi_id', ''),
        'whatsapp_link': config.get('whatsapp_link', 'https://wa.link/soyd4g'),
        'telegram_link': config.get('telegram_link', 'https://l1nq.link/?telegram=SUPERMAN_ADMIN1'),
        'normal_stock': normal_available,
        'verified_stock': verified_available
    })

@app.route('/api/submit-order', methods=['POST'])
def submit_order():
    """Submit order with UTR - Auto verification or Manual approval"""
    data = request.json
    utr = data.get('utr', '').strip()
    amount = float(data.get('amount', 0))
    quantity = int(data.get('quantity', 1))
    id_type = data.get('type', 'normal')
    screenshot_url = data.get('screenshot_url', '')
    
    print(f"\n📝 New order submission:")
    print(f"   UTR: {utr}")
    print(f"   Amount: ₹{amount}")
    print(f"   Quantity: {quantity}")
    print(f"   Type: {id_type}")
    
    # Validation
    if not utr or len(utr) < 1 or len(utr) > 12:
        return jsonify({
            'success': False,
            'error': 'UTR must be between 1-12 characters.'
        })
    
    if not utr.replace(' ', '').isalnum():
        return jsonify({
            'success': False,
            'error': 'Invalid UTR format. Use only letters and numbers.'
        })
    
    if amount <= 0 or quantity <= 0:
        return jsonify({
            'success': False,
            'error': 'Invalid amount or quantity.'
        })
    
    # Check if order already exists
    existing_order = orders_collection.find_one({'utr': utr})
    
    if existing_order:
        status = existing_order.get('status', 'pending')
        if status == 'approved':
            ids = existing_order.get('delivered_ids', [])
            return jsonify({
                'success': True,
                'approved': True,
                'ids': ids,
                'quantity': len(ids),
                'message': 'Order already approved. IDs ready.'
            })
        elif status == 'pending':
            return jsonify({
                'success': True,
                'pending': True,
                'vip': existing_order.get('is_vip', False),
                'message': 'Order is pending admin approval.'
            })
        elif status == 'rejected':
            return jsonify({
                'success': False,
                'error': 'Your order has been rejected. Please contact support.'
            })
    
    # Check if UTR already used
    if is_utr_used(utr):
        return jsonify({
            'success': False,
            'error': 'This UTR has already been used for a purchase.'
        })
    
    # Check stock availability
    collection = normal_ids_collection if id_type == 'normal' else verified_ids_collection
    available_count = collection.count_documents({'sold': False})
    
    if available_count < quantity:
        return jsonify({
            'success': False,
            'error': f'Not enough IDs available. Only {available_count} IDs in stock.'
        })
    
    # VIP UTR CHECK (Manual Approval Required)
    if is_vip_utr(utr):
        print(f"👑 VIP UTR detected: {utr} - Creating MANUAL approval order")
        
        try:
            pending_order = {
                'utr': utr,
                'amount': amount,
                'quantity': quantity,
                'type': id_type,
                'screenshot_url': screenshot_url,
                'date': datetime.now(),
                'status': 'pending',
                'is_vip': True,
                'auto_approved': False
            }
            
            result = orders_collection.insert_one(pending_order)
            order_id = str(result.inserted_id)
            
            print(f"✅ VIP Order created: {order_id}")
            print(f"⏳ Status: Pending manual approval (VIP UTR)")
            
            return jsonify({
                'success': True,
                'message': '👑 VIP UTR detected! Order submitted for manual approval.',
                'order_id': order_id,
                'pending': True,
                'vip': True
            })
            
        except Exception as e:
            print(f"❌ Error creating VIP order: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Error submitting order: {str(e)}'
            })
    
    # AUTO PAYMENT VERIFICATION (Non-VIP UTRs only)
    print(f"🔍 Auto-verifying payment for UTR: {utr}")
    
    success, message, actual_amount = verify_payment_bharatpe(utr, amount)
    
    if not success:
        print(f"❌ Payment verification failed: {message}")
        
        # Check if it's a "Payment not found" error - don't create pending order for random UTRs
        if 'Payment not found' in message or 'not found' in message.lower():
            print(f"🚫 Transaction not found in payment gateway - Rejecting order")
            return jsonify({
                'success': False,
                'error': 'Transaction not found. Please check your UTR and try again.'
            })
        
        # For other errors (amount mismatch, API issues), create pending order for manual review
        try:
            pending_order = {
                'utr': utr,
                'amount': amount,
                'quantity': quantity,
                'type': id_type,
                'screenshot_url': screenshot_url,
                'date': datetime.now(),
                'status': 'pending',
                'is_vip': False,
                'auto_approved': False,
                'verification_failed': True,
                'verification_message': message
            }
            
            result = orders_collection.insert_one(pending_order)
            order_id = str(result.inserted_id)
            
            print(f"⏳ Payment verification failed - Created pending order: {order_id}")
            
            return jsonify({
                'success': True,
                'message': 'Order submitted for manual verification.',
                'order_id': order_id,
                'pending': True,
                'vip': False
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Error submitting order: {str(e)}'
            })
    
    print(f"✅ Payment verified! Amount: ₹{actual_amount}")
    
    # Calculate how many IDs can be given based on actual amount
    config = config_collection.find_one({})
    price_per_id = config.get('normal_price', 200) if id_type == 'normal' else config.get('verified_price', 400)
    
    total_ids_allowed = int(actual_amount // price_per_id)
    
    if total_ids_allowed < quantity:
        return jsonify({
            'success': False,
            'error': f'Insufficient payment. You can get {total_ids_allowed} IDs with ₹{actual_amount}'
        })
    
    final_quantity = min(quantity, total_ids_allowed)
    
    try:
        print(f"📦 Allocating {final_quantity} {id_type} IDs...")
        delivered_ids = allocate_ids(id_type, final_quantity)
        
        if delivered_ids is None:
            return jsonify({
                'success': False,
                'error': f'Failed to allocate IDs. Stock might have changed.'
            })
        
        approved_order = {
            'utr': utr,
            'amount': actual_amount,
            'quantity': final_quantity,
            'type': id_type,
            'screenshot_url': screenshot_url,
            'date': datetime.now(),
            'status': 'approved',
            'delivered_ids': delivered_ids,
            'auto_approved': True,
            'is_vip': False
        }
        
        result = orders_collection.insert_one(approved_order)
        order_id = str(result.inserted_id)
        
        used_utrs_collection.insert_one({
            'utr': utr,
            'order_id': order_id,
            'date': datetime.now()
        })
        
        print(f"✅ Order auto-approved! ID: {order_id}")
        print(f"📦 Delivered {final_quantity} IDs")
        
        return jsonify({
            'success': True,
            'approved': True,
            'ids': delivered_ids,
            'quantity': final_quantity,
            'order_id': order_id,
            'message': f'Payment verified! {final_quantity} IDs delivered instantly.'
        })
        
    except Exception as e:
        print(f"❌ Error processing order: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error processing order: {str(e)}'
        })

@app.route('/api/check-order-status', methods=['POST'])
def check_order_status():
    """Check if order has been approved"""
    data = request.json
    utr = data.get('utr', '').strip()
    
    if not utr:
        return jsonify({'approved': False, 'error': 'UTR required'})
    
    order = orders_collection.find_one({'utr': utr})
    
    if not order:
        return jsonify({
            'approved': False,
            'pending': False,
            'error': 'Order not found'
        })
    
    status = order.get('status', 'pending')
    
    if status == 'approved':
        ids = order.get('delivered_ids', [])
        print(f"✅ Order approved for UTR: {utr}")
        print(f"📦 Delivering {len(ids)} IDs")
        
        return jsonify({
            'approved': True,
            'ids': ids,
            'quantity': len(ids),
            'order_id': str(order['_id']),
            'auto_approved': order.get('auto_approved', False)
        })
    
    elif status == 'pending':
        return jsonify({
            'approved': False,
            'pending': True,
            'vip': order.get('is_vip', False),
            'message': 'Order is pending admin approval'
        })
    
    elif status == 'rejected':
        return jsonify({
            'approved': False,
            'rejected': True,
            'error': 'Your order has been rejected. Please contact support.'
        })
    
    else:
        return jsonify({
            'approved': False,
            'error': f'Unknown order status: {status}'
        })

@app.route('/api/download-ids', methods=['POST'])
def download_ids():
    """Download IDs as .txt file"""
    data = request.json
    utr = data.get('utr', '').strip()
    
    if not utr:
        return jsonify({'success': False, 'error': 'UTR required'}), 400
    
    order = orders_collection.find_one({'utr': utr, 'status': 'approved'})
    
    if not order:
        return jsonify({'success': False, 'error': 'Order not found or not approved'}), 404
    
    ids = order.get('delivered_ids', [])
    quantity = len(ids)
    
    if not ids:
        return jsonify({'success': False, 'error': 'No IDs found'}), 404
    
    txt_content = f"ReplitStore - Your IDs\n"
    txt_content += f"UTR: {utr}\n"
    txt_content += f"Date: {order.get('date', datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}\n"
    txt_content += f"Type: {order.get('type', 'N/A').upper()}\n"
    txt_content += f"Quantity: {quantity}\n"
    txt_content += f"Auto-Approved: {'Yes' if order.get('auto_approved') else 'No'}\n"
    txt_content += "="*50 + "\n\n"
    
    for idx, id_info in enumerate(ids, 1):
        txt_content += f"ID #{idx}\n"
        txt_content += f"UserID: {id_info.get('userid', 'N/A')}\n"
        txt_content += f"Password: {id_info.get('password', 'N/A')}\n"
        txt_content += f"Email: {id_info.get('email', 'N/A')}\n"
        txt_content += f"Email Password: {id_info.get('email_password', 'N/A')}\n"
        txt_content += "-"*50 + "\n"
    
    txt_file = io.BytesIO(txt_content.encode('utf-8'))
    txt_file.seek(0)
    
    filename = f"replit{quantity}id.txt"
    
    print(f"📥 Downloading {filename} for UTR: {utr}")
    
    return send_file(
        txt_file,
        mimetype='text/plain',
        as_attachment=True,
        download_name=filename
    )

@app.route('/api/order-details/<order_id>')
def order_details(order_id):
    """Get order details by order ID"""
    try:
        order = orders_collection.find_one({'_id': ObjectId(order_id)})
        
        if order:
            order['_id'] = str(order['_id'])
            if isinstance(order.get('date'), datetime):
                order['date'] = order['date'].isoformat()
            return jsonify(order)
        
        return jsonify({'error': 'Order not found'}), 404
        
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/sms-proxy')
def sms_proxy():
    """Server-side proxy for SMSindia API — avoids CORS issues"""
    import requests as req
    params = request.args.to_dict()
    try:
        r = req.get('https://api.smsindia.pro/stubs/handler_api.php', params=params, timeout=15)
        return r.text, 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        return str(e), 500

@app.route('/api/health')
def health_check():
    """Check if service is running"""
    try:
        client.admin.command('ping')
        
        normal_count = normal_ids_collection.count_documents({'sold': False})
        verified_count = verified_ids_collection.count_documents({'sold': False})
        pending_count = orders_collection.count_documents({'status': 'pending'})
        auto_approved_count = orders_collection.count_documents({'auto_approved': True})
        
        config = config_collection.find_one({})
        payment_configured = bool(config.get('bharatpe_token') and config.get('bharatpe_mid'))
        
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'normal_stock': normal_count,
            'verified_stock': verified_count,
            'pending_orders': pending_count,
            'auto_approved_orders': auto_approved_count,
            'payment_gateway': 'configured' if payment_configured else 'not configured'
        })
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

# ─── USER REGISTER ───────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    email     = data.get('email', '').strip().lower()
    telegram  = data.get('telegram', '').strip()
    whatsapp  = data.get('whatsapp', '').strip()
    password  = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'success': False, 'error': 'Email aur password required hai.'})
    if len(password) < 6:
        return jsonify({'success': False, 'error': 'Password kam se kam 6 characters ka hona chahiye.'})
    if users_collection.find_one({'email': email}):
        return jsonify({'success': False, 'error': 'Yeh email already registered hai.'})

    user = {
        'email': email,
        'telegram': telegram,
        'whatsapp': whatsapp,
        'password': password,
        'balance': 0,
        'created_at': datetime.now()
    }
    result = users_collection.insert_one(user)
    return jsonify({'success': True, 'message': 'Registration successful!'})

# ─── USER LOGIN ──────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
def login():
    data     = request.json
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'success': False, 'error': 'Email aur password required hai.'})

    user = users_collection.find_one({'email': email, 'password': password})
    if not user:
        return jsonify({'success': False, 'error': 'Email ya password galat hai.'})

    return jsonify({
        'success': True,
        'user': {
            'id': str(user['_id']),
            'email': user['email'],
            'telegram': user.get('telegram', ''),
            'whatsapp': user.get('whatsapp', ''),
            'balance': user.get('balance', 0)
        }
    })

# ─── GET USER BALANCE ─────────────────────────────────────
@app.route('/api/user-balance', methods=['POST'])
def user_balance():
    data    = request.json
    user_id = data.get('user_id', '')
    try:
        user = users_collection.find_one({'_id': ObjectId(user_id)})
        if not user:
            return jsonify({'success': False, 'error': 'User not found'})
        return jsonify({'success': True, 'balance': user.get('balance', 0)})
    except:
        return jsonify({'success': False, 'error': 'Invalid user id'})

# ─── SERVICE PRICES (admin panel se set hoga) ─────────────
@app.route('/api/service-prices')
def get_service_prices():
    prices = list(service_prices_collection.find({}, {'_id': 0}))
    return jsonify({'success': True, 'prices': prices})

# ─── RECHARGE — UTR submit, balance user ke account mein ──
@app.route('/api/recharge', methods=['POST'])
def recharge():
    data    = request.json
    utr     = data.get('utr', '').strip()
    user_id = data.get('user_id', '')

    if not utr or len(utr) < 1 or len(utr) > 12:
        return jsonify({'success': False, 'error': 'UTR 1-12 characters ka hona chahiye.'})
    if not utr.replace(' ', '').isalnum():
        return jsonify({'success': False, 'error': 'UTR sirf letters aur numbers ka hona chahiye.'})

    # Already used?
    if used_utrs_collection.find_one({'utr': utr}):
        return jsonify({'success': False, 'error': 'Yeh UTR pehle already use ho chuka hai.'})

    # Existing order?
    existing = orders_collection.find_one({'utr': utr})
    if existing:
        status = existing.get('status', 'pending')
        if status == 'approved':
            return jsonify({'success': False, 'error': 'Yeh UTR pehle already use ho chuka hai.'})
        if status == 'pending':
            return jsonify({'success': True, 'pending': True, 'vip': existing.get('is_vip', False),
                            'message': 'Order already pending hai.'})

    # VIP UTR?
    if is_vip_utr(utr):
        order = {
            'utr': utr, 'user_id': user_id, 'date': datetime.now(),
            'status': 'pending', 'is_vip': True, 'auto_approved': False, 'type': 'recharge'
        }
        orders_collection.insert_one(order)
        return jsonify({'success': True, 'pending': True, 'vip': True,
                        'message': 'VIP UTR — manual approval hogi.'})

    # BharatPe verify
    success, message, actual_amount = verify_payment_bharatpe(utr, 1)

    if not success:
        if 'not found' in message.lower() or 'Payment not found' in message:
            return jsonify({'success': False, 'error': 'Transaction nahi mila. UTR check karke dobara try karo.'})
        # Other errors → pending
        order = {
            'utr': utr, 'user_id': user_id, 'amount': 0, 'date': datetime.now(),
            'status': 'pending', 'is_vip': False, 'auto_approved': False, 'type': 'recharge',
            'verification_message': message
        }
        orders_collection.insert_one(order)
        return jsonify({'success': True, 'pending': True, 'vip': False,
                        'message': 'Order manual verification ke liye submit ho gaya.'})

    # Auto approve — add balance
    try:
        users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$inc': {'balance': actual_amount}}
        )
        order = {
            'utr': utr, 'user_id': user_id, 'amount': actual_amount, 'date': datetime.now(),
            'status': 'approved', 'is_vip': False, 'auto_approved': True, 'type': 'recharge'
        }
        result = orders_collection.insert_one(order)
        used_utrs_collection.insert_one({'utr': utr, 'order_id': str(result.inserted_id), 'date': datetime.now()})
        user = users_collection.find_one({'_id': ObjectId(user_id)})
        return jsonify({'success': True, 'approved': True, 'amount': actual_amount,
                        'new_balance': user.get('balance', 0),
                        'message': f'₹{actual_amount} balance add ho gaya!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ─── CHECK RECHARGE STATUS (pending poll) ─────────────────
@app.route('/api/check-recharge-status', methods=['POST'])
def check_recharge_status():
    data    = request.json
    utr     = data.get('utr', '').strip()
    user_id = data.get('user_id', '')

    order = orders_collection.find_one({'utr': utr, 'type': 'recharge'})
    if not order:
        return jsonify({'approved': False, 'pending': False, 'error': 'Order not found'})

    status = order.get('status', 'pending')
    if status == 'approved':
        try:
            user = users_collection.find_one({'_id': ObjectId(user_id)})
            return jsonify({'approved': True, 'new_balance': user.get('balance', 0) if user else 0,
                            'amount': order.get('amount', 0)})
        except:
            return jsonify({'approved': True, 'new_balance': 0})
    elif status == 'pending':
        return jsonify({'approved': False, 'pending': True, 'vip': order.get('is_vip', False)})
    elif status == 'rejected':
        return jsonify({'approved': False, 'rejected': True, 'error': 'Order reject ho gaya.'})
    return jsonify({'approved': False})

# ─── INSTALL APP — APK download ───────────────────────────
@app.route('/api/download-apk')
def download_apk():
    config = config_collection.find_one({})
    apk_url = config.get('apk_url', '')
    if not apk_url:
        return jsonify({'success': False, 'error': 'APK abhi available nahi hai.'}), 404
    from flask import redirect
    return redirect(apk_url)


if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    print("\n" + "="*50)
    print("🚀 ReplitStore Customer Site Starting...")
    print("="*50)
    print(f"🌐 Customer Site: http://localhost:5000/")
    print(f"📊 Health Check: http://localhost:5000/api/health")
    print(f"⚡ Auto Payment: ENABLED")
    print(f"👑 VIP UTR: ENABLED")
    print("="*50 + "\n")
    app.run(debug=True, host='0.0.0.0', port=5000)


# ─── VIRTUAL ADMIN PANEL ──────────────────────────────────
@app.route('/virtual-admin')
@app.route('/virtual-admin.html')
def virtual_admin():
    return render_template('virtual_admin.html')

@app.route('/api/admin/vn-config', methods=['GET'])
def get_vn_config():
    """Get virtual number admin config"""
    config = config_collection.find_one({}, {'_id': 0})
    prices = list(service_prices_collection.find({}, {'_id': 0}))
    return jsonify({
        'success': True,
        'api_url': config.get('sms_api_url', 'https://api.smsindia.pro/stubs/handler_api.php'),
        'api_key': config.get('sms_api_key', 'a362879eb1515c86208a58f8557c9a7a3ab0'),
        'qr_code': config.get('qr_code', ''),
        'upi_id': config.get('upi_id', ''),
        'whatsapp_link': config.get('whatsapp_link', ''),
        'telegram_link': config.get('telegram_link', ''),
        'apk_url': config.get('apk_url', ''),
        'prices': prices
    })

@app.route('/api/admin/update-api', methods=['POST'])
def update_api():
    """Update SMS API URL and key"""
    data = request.json
    api_url = data.get('api_url', '').strip()
    api_key = data.get('api_key', '').strip()
    if not api_url or not api_key:
        return jsonify({'success': False, 'error': 'API URL aur API Key dono required hain.'})
    config_collection.update_one({}, {'$set': {'sms_api_url': api_url, 'sms_api_key': api_key}}, upsert=True)
    return jsonify({'success': True, 'message': 'API settings save ho gayi!'})

@app.route('/api/admin/update-payment', methods=['POST'])
def update_payment():
    """Update QR code and UPI ID"""
    data = request.json
    qr_code = data.get('qr_code', '').strip()
    upi_id  = data.get('upi_id', '').strip()
    config_collection.update_one({}, {'$set': {'qr_code': qr_code, 'upi_id': upi_id}}, upsert=True)
    return jsonify({'success': True, 'message': 'Payment settings save ho gayi!'})

@app.route('/api/admin/set-price', methods=['POST'])
def set_price():
    """Set price for country+operator+service combo"""
    data = request.json
    country_id   = data.get('country_id', '')
    country_name = data.get('country_name', '')
    operator     = data.get('operator', '')
    service_code = data.get('service_code', '')
    service_name = data.get('service_name', '')
    price        = data.get('price', 0)
    if not country_id or not operator or not service_code or not price:
        return jsonify({'success': False, 'error': 'Sab fields required hain.'})
    service_prices_collection.update_one(
        {'country_id': country_id, 'operator': str(operator), 'service_code': service_code},
        {'$set': {
            'country_id': country_id, 'country_name': country_name,
            'operator': str(operator), 'service_code': service_code,
            'service_name': service_name, 'price': float(price)
        }},
        upsert=True
    )
    return jsonify({'success': True, 'message': f'{service_name} ka price set ho gaya: ₹{price}'})

@app.route('/api/admin/delete-price', methods=['POST'])
def delete_price():
    """Delete a service price"""
    data = request.json
    country_id   = data.get('country_id', '')
    operator     = data.get('operator', '')
    service_code = data.get('service_code', '')
    service_prices_collection.delete_one({
        'country_id': country_id, 'operator': str(operator), 'service_code': service_code
    })
    return jsonify({'success': True, 'message': 'Price delete ho gaya.'})

@app.route('/api/admin/update-links', methods=['POST'])
def update_links():
    """Update whatsapp, telegram, apk url"""
    data = request.json
    update = {}
    if 'whatsapp_link' in data: update['whatsapp_link'] = data['whatsapp_link'].strip()
    if 'telegram_link' in data: update['telegram_link'] = data['telegram_link'].strip()
    if 'apk_url'       in data: update['apk_url']       = data['apk_url'].strip()
    config_collection.update_one({}, {'$set': update}, upsert=True)
    return jsonify({'success': True, 'message': 'Links update ho gaye!'})