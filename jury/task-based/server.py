#!/usr/bin/env python

"""server.py -- the main flask server module"""

import dataset
import json
import random
import time
import hashlib
import datetime
import os
import dateutil.parser
import bleach
import codecs

from base64 import b64decode
from functools import wraps

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlite3 import Connection as SQLite3Connection
from werkzeug.contrib.fixers import ProxyFix

from flask import Flask
from flask import jsonify
from flask import make_response
from flask import redirect
from flask import render_template
from flask import request
from flask import session
from flask import url_for
from flask import Response

app = Flask(__name__, static_folder='static', static_url_path='')

db = None
lang = None
config = None

descAllowedTags = bleach.ALLOWED_TAGS + ['br', 'pre']

def start_required(f):
    """Ensures that an tournament is logged in"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        userCount = db['users'].count()
        user = get_user()
        if user["isAdmin"] == False and (datetime.datetime.today() < config['startTime'] and userCount != 0):
            return redirect('/error/not_started')
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    """Ensures that an user is logged in"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('error', msg='login_required'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Ensures that an user is logged in"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('error', msg='login_required'))
        user = get_user()
        if user["isAdmin"] == False:
            return redirect(url_for('error', msg='admin_required'))
        return f(*args, **kwargs)
    return decorated_function

def get_user():
    """Looks up the current user in the database"""

    login = 'user_id' in session
    if login:
        return db['users'].find_one(id=session['user_id'])

    return None

def get_task(tid):
    """Finds a task with a given category and score"""

    task = db.query("SELECT t.*, c.name cat_name FROM tasks t JOIN categories c on c.id = t.category WHERE t.id = :tid",
            tid=tid)

    return task.next()

def get_flags():
    """Returns the flags of the current user"""

    flags = db.query('''select f.task_id from flags f
        where f.user_id = :user_id''',
        user_id=session['user_id'])
    return [f['task_id'] for f in list(flags)]

def get_total_completion_count():
    """Returns dictionary where key is task id and value is the number of users who have submitted the flag"""

    c = db.query("select t.id, count(t.id) count from tasks t join flags f on t.id = f.task_id group by t.id;")

    res = {}
    for r in c:
        res.update({r['id']: r['count']})

    return res

@app.route('/error/<msg>')
def error(msg):
    """Displays an error message"""

    if msg in lang['error']:
        message = lang['error'][msg]
    else:
        message = lang['error']['unknown']

    user = get_user()

    render = render_template('frame.html', lang=lang, page='error.html',
        message=message, user=user)
    return make_response(render)

def session_login(email):
    """Initializes the session with the current user's id"""
    user = db['users'].find_one(email=email)
    session['user_id'] = user['id']

@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """ Enforces sqlite foreign key constrains """
    if isinstance(dbapi_connection, SQLite3Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

@app.route('/login', methods = ['POST'])
def login():
    """Attempts to log the user in"""

    from werkzeug.security import check_password_hash

    email = request.form['email']
    password = request.form['password']
    print(email)
    user = db['users'].find_one(email=email)

    if user is None:
        return redirect('/error/invalid_credentials')

    if check_password_hash(user['password'], password):
        session_login(email)
        return redirect('/about')

    return redirect('/error/invalid_credentials')

@app.route('/register')
def register():
    """Displays the register form"""

    userCount = db['users'].count()
    if datetime.datetime.today() < config['startTime'] and userCount != 0:
        return redirect('/error/not_started')

    # Render template
    render = render_template('frame.html', lang=lang,
        page='register.html', login=False)
    return make_response(render)

@app.route('/register/submit', methods = ['POST'])
def register_submit():
    """Attempts to register a new user"""

    from werkzeug.security import generate_password_hash

    username = request.form['user']
    email = request.form['email']
    affilation = request.form['affilation']
    lineup = request.form['lineup']
    password = request.form['password']

    if not username:
        return redirect('/error/empty_user')

    user_found = db['users'].find_one(username=username)
    if user_found:
        return redirect('/error/already_registered')

    if not (email and affilation and lineup and password):
        return redirect('/error/bad_request')

    isAdmin = False
    isHidden = False
    userCount = db['users'].count()

    #if no users, make first user admin
    if userCount == 0:
        isAdmin = True
        isHidden = True

    new_user = dict(username=username, email=email,
        affilation=affilation, lineup=lineup,
        password=generate_password_hash(password), isAdmin=isAdmin,
        isHidden=isHidden)
    db['users'].insert(new_user)

    # Set up the user id for this session
    session_login(email)
    return redirect('/about')

@app.route('/tasks')
@login_required
@start_required
def tasks():
    """Displays all the tasks in a grid"""

    user = get_user()
    userCount = db['users'].count(isHidden=0)
    isAdmin = user['isAdmin']

    categories = db['categories']
    catCount = categories.count()

    flags = get_flags()

    tasks = db.query("SELECT * FROM tasks ORDER BY category, score")
    tasks = list(tasks)
    taskCompletedCount = get_total_completion_count()

    grid = []

    for cat in categories:
        cTasks = [x for x in tasks if x['category'] == cat['id']]
        gTasks = []

        gTasks.append(cat)
        for task in cTasks:
            tid = task['id']
            if tid in taskCompletedCount:
                percentComplete = (float(taskCompletedCount[tid]) / userCount) * 100
            else:
                percentComplete = 0

            #hax for bad css (if 100, nothing will show)
            if percentComplete == 100:
                percentComplete = 99.99

            task['percentComplete'] = percentComplete

            task['isComplete'] = tid in flags
            gTasks.append(task)

        if isAdmin:
            gTasks.append({'add': True, 'category': cat['id']})

        grid.append(gTasks)

    # Render template
    render = render_template('frame.html', lang=lang, page='tasks.html',
        user=user, categories=categories, grid=grid)
    return make_response(render)

@app.route('/addcat/', methods=['GET'])
@admin_required
def addcat():
    user = get_user()
    render = render_template('frame.html', lang=lang, user=user, page='addcat.html')
    return make_response(render)

@app.route('/makedump', methods=['GET'])
@admin_required
def makedump():
    result = db['users'].all()
    fh = open('users.json', 'wb')
    dataset.freeze(result, format='json', fileobj=fh)
    user = get_user()
    render = render_template('frame.html', lang=lang,
        page='main.html', user=user)
    return make_response(render)

@app.route('/addcat/', methods=['POST'])
@admin_required
def addcatsubmit():
    try:
        name = bleach.clean(request.form['name'], tags=[])
    except KeyError:
        return redirect('/error/form')
    else:
        categories = db['categories']
        categories.insert(dict(name=name))
        return redirect('/tasks')

@app.route('/editcat/<id>/', methods=['GET'])
@admin_required
def editcat(id):
    user = get_user()
    category = db['categories'].find_one(id=id)
    render = render_template('frame.html', lang=lang, user=user, category=category, page='editcat.html')
    return make_response(render)

@app.route('/editcat/<catId>/', methods=['POST'])
@admin_required
def editcatsubmit(catId):
    try:
        name = bleach.clean(request.form['name'], tags=[])
    except KeyError:
        return redirect('/error/form')
    else:
        categories = db['categories']
        categories.update(dict(name=name, id=catId), ['id'])
        return redirect('/tasks')

@app.route('/editcat/<catId>/delete', methods=['GET'])
@admin_required
def deletecat(catId):
    category = db['categories'].find_one(id=catId)

    user = get_user()
    render = render_template('frame.html', lang=lang, user=user, page='deletecat.html', category=category)
    return make_response(render)

@app.route('/editcat/<catId>/delete', methods=['POST'])
@admin_required
def deletecatsubmit(catId):
    db['categories'].delete(id=catId)
    return redirect('/tasks')

@app.route('/addtask/<cat>/', methods=['GET'])
@admin_required
def addtask(cat):
    category = db['categories'].find_one(id=cat)

    user = get_user()

    render = render_template('frame.html', lang=lang, user=user,
            cat_name=category['name'], cat_id=category['id'], page='addtask.html')
    return make_response(render)

@app.route('/addtask/<cat>/', methods=['POST'])
@admin_required
def addtasksubmit(cat):
    try:
        name = bleach.clean(request.form['name'], tags=[])
        desc = bleach.clean(request.form['desc'], tags=descAllowedTags)
        hint = bleach.clean(request.form['hint'], tags=descAllowedTags)
        solve = bleach.clean(request.form['solve'], tags=descAllowedTags)
        author = bleach.clean(request.form['author'], tags=[])
        category = int(request.form['category'])
        score = int(request.form['score'])
        flag = request.form['flag']
    except KeyError:
        return redirect('/error/form')

    else:
        tasks = db['tasks']
        task = dict(
                name=name,
                desc=desc,
                hint=hint,
                solve=solve,
                author=author,
                category=category,
                score=score,
                flag=flag)
        file = request.files['file']

        if file:
            filename, ext = os.path.splitext(file.filename)
            #hash current time for file name
            filename = hashlib.md5(str(datetime.datetime.utcnow()).encode('utf-8')).hexdigest()
            #if upload has extension, append to filename
            if ext:
                filename = filename + ext
            file.save(os.path.join("static/files/", filename))
            task["file"] = filename

        tasks.insert(task)
        return redirect('/tasks')

@app.route('/tasks/<tid>/edit', methods=['GET'])
@admin_required
def edittask(tid):
    user = get_user()

    task = db["tasks"].find_one(id=tid);
    category = db["categories"].find_one(id=task['category'])

    render = render_template('frame.html', lang=lang, user=user,
            cat_name=category['name'], cat_id=category['id'],
            page='edittask.html', task=task)
    return make_response(render)

@app.route('/tasks/<tid>/edit', methods=['POST'])
@admin_required
def edittasksubmit(tid):
    try:
        name = bleach.clean(request.form['name'], tags=[])
        desc = bleach.clean(request.form['desc'], tags=descAllowedTags)
        hint = bleach.clean(request.form['hint'], tags=descAllowedTags)
        solve = bleach.clean(request.form['solve'], tags=descAllowedTags)
        author = bleach.clean(request.form['author'], tags=[])
        category = int(request.form['category'])
        score = int(request.form['score'])
        flag = request.form['flag']
    except KeyError:
        return redirect('/error/form')

    else:
        tasks = db['tasks']
        task = tasks.find_one(id=tid)
        task['id']=tid
        task['name']=name
        task['desc']=desc
        task['hint']=hint
        task['solve']=solve
        task['author']=author
        task['category']=category
        task['score']=score

        #only replace flag if value specified
        if flag:
            task['flag']=flag

        file = request.files['file']

        if file:
            filename, ext = os.path.splitext(file.filename)
            #hash current time for file name
            filename = hashlib.md5(str(datetime.datetime.utcnow()).encode('utf-8')).hexdigest()
            #if upload has extension, append to filename
            if ext:
                filename = filename + ext
            file.save(os.path.join("static/files/", filename))

            #remove old file
            if task['file']:
                os.remove(os.path.join("static/files/", task['file']))

            task["file"] = filename

        tasks.update(task, ['id'])
        return redirect('/tasks')

@app.route('/tasks/<tid>/delete', methods=['GET'])
@admin_required
def deletetask(tid):
    tasks = db['tasks']
    task = tasks.find_one(id=tid)

    user = get_user()
    render = render_template('frame.html', lang=lang, user=user, page='deletetask.html', task=task)
    return make_response(render)

@app.route('/tasks/<tid>/delete', methods=['POST'])
@admin_required
def deletetasksubmit(tid):
    db['tasks'].delete(id=tid)
    return redirect('/tasks')

@app.route('/tasks/<tid>/')
@login_required
def task(tid):
    """Displays a task with a given category and score"""

    user = get_user()

    task = get_task(tid)
    if not task:
        return redirect('/error/task_not_found')

    flags = get_flags()
    task_done = task['id'] in flags

    solutions = db['flags'].find(task_id=task['id'])
    solutions = len(list(solutions))

    # Render template
    render = render_template('frame.html', lang=lang, page='task.html',
        task_done=task_done, login=login, solutions=solutions,
        user=user, category=task["cat_name"], task=task, score=task["score"])
    return make_response(render)

@app.route('/submit/<tid>/<flag>')
@login_required
def submit(tid, flag):
    """Handles the submission of flags"""

    print(flag)
    user = get_user()

    task = get_task(tid)
    flags = get_flags()
    print(task['flag'])
    task_done = task['id'] in flags

    result = {'success': False}
    if not task_done and task['flag'] == b64decode(flag).decode("utf-8"):

        timestamp = int(time.time() * 1000)
        ip = request.remote_addr
        print ("flag submitter ip: {}".format(ip))

        # Insert flag
        new_flag = dict(task_id=task['id'], user_id=session['user_id'],
            score=task["score"], timestamp=timestamp, ip=ip)
        db['flags'].insert(new_flag)

        result['success'] = True

    return jsonify(result)

@app.route('/scoreboard')
@login_required
def scoreboard():
    """Displays the scoreboard"""

    user = get_user()
    scores = db.query('''select u.username, u.affilation, u.logo, ifnull(sum(f.score), 0) as score,
        max(timestamp) as last_submit from users u left join flags f
        on u.id = f.user_id where u.isHidden = 0 group by u.username
        order by score desc, last_submit asc''')

    scores = list(scores)

    # Render template
    render = render_template('frame.html', lang=lang, page='scoreboard.html',
        user=user, scores=scores)
    return make_response(render)

@app.route('/scoreboard.json')
def scoreboard_json():
    scores = db.query('''select u.username, ifnull(sum(f.score), 0) as score,
        max(timestamp) as last_submit from users u left join flags f
        on u.id = f.user_id where u.isHidden = 0 group by u.username
        order by score desc, last_submit asc''')

    scores = list(scores)

    return Response(json.dumps(scores), mimetype='application/json')

@app.route('/about')
@login_required
def about():
    """Displays the about menu"""

    user = get_user()
    render = render_template('frame.html', lang=lang, page='about.html',
        user=user)
    return make_response(render)

# @app.route('/settings')
# @login_required
# def settings():
#     user = get_user()
#     render = render_template('frame.html', lang=lang, page='settings.html',
#         user=user)
#     return make_response(render)
#
# @app.route('/settings', methods = ['POST'])
# @login_required
# def settings_submit():
#     from werkzeug.security import check_password_hash
#     from werkzeug.security import generate_password_hash
#
#     user = get_user()
#     try:
#         old_pw = request.form['old-pw']
#         new_pw = request.form['new-pw']
#         email = request.form['email']
#     except KeyError:
#         return redirect('/error/form')
#
#     if old_pw and check_password_hash(user['password'], old_pw):
#         if new_pw:
#             user['password'] = generate_password_hash(new_pw)
#         if email:
#             user['email'] = email
#     else:
#         return redirect('/error/invalid_password')
#
#     db["users"].update(user, ['id'])
#     return redirect('/tasks')

@app.route('/logout')
@login_required
def logout():
    """Logs the current user out"""

    del session['user_id']
    return redirect('/')

@app.route('/')
def index():
    """Displays the main page"""

    user = get_user()

    # Render template
    render = render_template('frame.html', lang=lang,
        page='main.html', user=user)
    return make_response(render)

"""Initializes the database and sets up the language"""

# Load config
config_str = open('config.json', 'r').read()
config = json.loads(config_str)

app.secret_key = config['secret_key']

# Convert start date to python object
if config['startTime']:
    config['startTime'] = dateutil.parser.parse(config['startTime'])
else:
    config['startTime'] = datetime.datetime.min

# Load language
lang_str = codecs.open(config['language_file'], 'r', "utf-8").read()
lang = json.loads(lang_str)

# Only a single language is supported for now
lang = lang[config['language']]

# Connect to database
db = dataset.connect(config['db'])

if config['isProxied']:
    app.wsgi_app = ProxyFix(app.wsgi_app)

if __name__ == '__main__':
    # Start web server
    app.run(host=config['host'], port=config['port'],
        debug=config['debug'], threaded=True)
