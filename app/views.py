from celery.result import AsyncResult
from datetime import datetime
from flask import Blueprint
from flask import render_template, redirect, url_for, session, request
import json
from kiteconnect import KiteConnect
import os
from . import forms
from . import tasks
from . import storage as sg

bp = Blueprint('main', __name__, url_prefix='')

TOKEN_PATH = "token.json"
BASE_URL = "https://kite.zerodha.com/connect/login"
KITE_API_KEY = os.environ["kite_api_key"]
LOGIN_URL = f"{BASE_URL}?api_key={KITE_API_KEY}"


def get_kite_client():
    kite = KiteConnect(KITE_API_KEY)
    if "access_token" in session:
        kite.set_access_token(session["access_token"])

    return kite


def get_token():
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH) as f:
            data = json.load(f)
            return data["access_token"]
    else:
        return None


def is_token_valid():
    valid = True
    if os.path.exists(TOKEN_PATH):
        mtime = datetime.fromtimestamp(os.path.getmtime(TOKEN_PATH))
        now = datetime.now()
        if now > mtime and (
                now.date() != mtime.date() or
                now.hour > 8 and mtime.hour < 8):
            valid = False
    else:
        valid = False

    return valid


@bp.get('/login')
def login():
    request_token = request.args.get("request_token")
    if request_token:
        kite = get_kite_client()
        data = kite.generate_session(request_token,
                                     api_secret=os.environ["kite_api_secret"])
        session["access_token"] = data["access_token"]
        with open(TOKEN_PATH, "w") as f:
            json.dump({"access_token": data["access_token"]}, f)

    return redirect(url_for('main.index'))


@bp.route('/', methods=['GET', 'POST'])
def index():
    form = forms.TradeForm()
    if form.validate_on_submit():
        order_info = {
            'instrument': form.instrument.data,
            'lots': form.lots.data,
            'stoploss': form.stoploss.data,
            'product': form.product.data,
            'expiry': form.expiry.data,
            'date': str(datetime.now())
            }
        sg.trades.insert_one(order_info)
        oid = order_info["_id"]
        order_info["_id"] = str(order_info["_id"])
        result = tasks.initiate_trade(order_info)
        print("initiate trade result is ", result, type(result), result.id,
              type(result.id))
        sg.trades.update_one({'_id': oid}, {"$set": {'task_id': result.id}})
        return redirect(url_for('main.get_trades'))

    if is_token_valid():
        kite = get_kite_client()
        kite.set_access_token(get_token())
        nf_price = kite.ltp('NSE:NIFTY 50')['NSE:NIFTY 50']['last_price']
        bnf_price = kite.ltp('NSE:NIFTY BANK')['NSE:NIFTY BANK']['last_price']
        return render_template('index.html', form=form,
                               spot=[nf_price, bnf_price])
    else:
        return """<a href="{LOGIN_URL}"><h1>Login</h1></a>""".format(LOGIN_URL=LOGIN_URL)


@bp.route('/trades/')
def get_trades():
    trade_list = list(sg.trades.find())
    for trade in trade_list:
        trade.pop("_id")
    return render_template('trades.html', trade_list=trade_list)


def _get_status_from_db(id):
    return sg.trades.find_one({"task_id": id})["status"]


@bp.route('/result/<id>')
def result(id):
    result = AsyncResult(id)
    ready = result.ready()
    return {
        "ready": ready,
        "successful": result.successful() if ready else None,
        "value": result.get() if ready else _get_status_from_db(id)
    }
