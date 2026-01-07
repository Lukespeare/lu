import os
import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# -------------------------- 初始化配置 --------------------------
app = Flask(__name__)
# 基础配置
app.config['SECRET_KEY'] = 'paper_system_2025'  # 加密密钥
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # 关闭修改跟踪
# 上传配置
UPLOAD_FOLDER = os.path.join(app.root_path, 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20MB上传限制
# 禁用模板缓存（开发模式）
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

# 初始化数据库
db = SQLAlchemy(app)

# 初始化登录管理器
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# 创建uploads文件夹
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# 允许的上传文件类型
ALLOWED_EXTENSIONS = {'doc', 'docx', 'pdf'}

# -------------------------- 辅助函数 --------------------------
def allowed_file(filename):
    """检查文件类型"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# -------------------------- 数据库模型 --------------------------
class User(UserMixin, db.Model):
    """用户模型（作者/编辑/专家/主编）"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)  # 账号
    password_hash = db.Column(db.String(200), nullable=False)  # 密码哈希
    name = db.Column(db.String(50), nullable=False)  # 姓名
    phone = db.Column(db.String(20))  # 手机号
    email = db.Column(db.String(100))  # 邮箱
    role = db.Column(db.String(20), nullable=False)  # author/editor/expert/chief

    def set_password(self, password):
        """设置密码（加密）"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """验证密码"""
        return check_password_hash(self.password_hash, password)

class Manuscript(db.Model):
    """稿件模型"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)  # 文章题目
    author_name = db.Column(db.String(50), nullable=False)  # 作者姓名
    keywords = db.Column(db.String(200), nullable=False)  # 关键词
    file_path = db.Column(db.String(200), nullable=False)  # 稿件文件路径
    # 状态说明：
    # pending_assign: 待分配 | pending_review: 待评审 | rejected_review: 专家拒审
    # reviewed: 审核通过 | rejected: 编辑退稿 | accepted: 编辑录用 | published: 主编发表
    status = db.Column(db.String(20), default='pending_assign')  
    create_time = db.Column(db.DateTime, default=datetime.datetime.now)  # 投稿时间
    publish_time = db.Column(db.Date)  # 发表日期
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # 投稿作者ID
    expert_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # 评审专家ID
    sort_num = db.Column(db.Integer)  # 发表排序号

    # 关联关系
    author = db.relationship('User', foreign_keys=[author_id], backref='manuscripts')
    expert = db.relationship('User', foreign_keys=[expert_id])

class Review(db.Model):
    """评审意见模型"""
    id = db.Column(db.Integer, primary_key=True)
    manuscript_id = db.Column(db.Integer, db.ForeignKey('manuscript.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False, default=0)  # 评分（0-100）
    opinion = db.Column(db.String(200), nullable=False, default='')  # 评审意见（200字内）
    reject_reason = db.Column(db.String(200))  # 拒审原因
    create_time = db.Column(db.DateTime, default=datetime.datetime.now)

    # 关联关系
    manuscript = db.relationship('Manuscript', backref='reviews')

# -------------------------- 登录/注册 --------------------------
@login_manager.user_loader
def load_user(user_id):
    """加载用户"""
    return User.query.get(int(user_id))

@app.route('/')
def index():
    """根路由重定向到登录页"""
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """纯登录界面（拆分注册）"""
    if current_user.is_authenticated:
        return redirect(url_for(f'{current_user.role}_index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        user = User.query.filter_by(username=username, role=role).first()
        
        if not user or not user.check_password(password):
            flash('账号/密码/角色错误！')
            return render_template('login.html')
        
        login_user(user)
        return redirect(url_for(f'{role}_index'))
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """纯注册界面（仅注册作者）"""
    if current_user.is_authenticated:
        return redirect(url_for(f'{current_user.role}_index'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        confirm_pwd = request.form['confirm_pwd'].strip()
        name = request.form['name'].strip()
        
        # 校验
        if not username or not password or not confirm_pwd or not name:
            flash('所有字段不能为空！')
            return render_template('register.html')
        if password != confirm_pwd:
            flash('两次密码不一致！')
            return render_template('register.html')
        if User.query.filter_by(username=username).first():
            flash('账号已存在！')
            return render_template('register.html')
        
        # 创建作者账号
        user = User(
            username=username,
            name=name,
            role='author'  # 注册仅能创建作者账号
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('注册成功！请登录')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    """登出"""
    logout_user()
    flash('已成功登出！')
    return redirect(url_for('login'))

# -------------------------- 作者模块 --------------------------
@app.route('/author/index')
@login_required
def author_index():
    """作者首页"""
    if current_user.role != 'author':
        flash('无权限！')
        return redirect(url_for('login'))
    return render_template('author/index.html')

# 作者投稿步骤1：题目
@app.route('/author/submit/step1', methods=['GET', 'POST'])
@login_required
def author_submit_step1():
    if current_user.role != 'author':
        flash('无权限！')
        return redirect(url_for('login'))
    
    if 'submit_data' not in session:
        session['submit_data'] = {}
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if not title:
            flash('文章题目不能为空！')
            return render_template('author/submit_step1.html', data=session['submit_data'])
        
        session['submit_data']['title'] = title
        session.modified = True
        return redirect(url_for('author_submit_step2'))
    
    return render_template('author/submit_step1.html', data=session.get('submit_data', {}))

# 作者投稿步骤2：作者信息
@app.route('/author/submit/step2', methods=['GET', 'POST'])
@login_required
def author_submit_step2():
    if current_user.role != 'author' or 'submit_data' not in session:
        return redirect(url_for('author_submit_step1'))
    
    if request.method == 'POST':
        author_name = request.form.get('author_name', '').strip()
        if not author_name:
            flash('作者姓名不能为空！')
            return render_template('author/submit_step2.html', data=session['submit_data'])
        
        session['submit_data']['author_name'] = author_name
        session.modified = True
        return redirect(url_for('author_submit_step3'))
    
    return render_template('author/submit_step2.html', data=session['submit_data'])

# 作者投稿步骤3：关键词
@app.route('/author/submit/step3', methods=['GET', 'POST'])
@login_required
def author_submit_step3():
    if current_user.role != 'author' or 'submit_data' not in session:
        return redirect(url_for('author_submit_step1'))
    
    if request.method == 'POST':
        keywords = request.form.get('keywords', '').strip()
        if not keywords:
            flash('关键词不能为空！')
            return render_template('author/submit_step3.html', data=session['submit_data'])
        
        session['submit_data']['keywords'] = keywords
        session.modified = True
        return redirect(url_for('author_submit_step4'))
    
    return render_template('author/submit_step3.html', data=session['submit_data'])

# 作者投稿步骤4：上传文件
@app.route('/author/submit/step4', methods=['GET', 'POST'])
@login_required
def author_submit_step4():
    if current_user.role != 'author':
        flash('无权限！')
        return redirect(url_for('login'))
    
    if 'submit_data' not in session:
        session['submit_data'] = {'title': '', 'author_name': '', 'keywords': ''}
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('请选择要上传的文件！')
            return render_template('author/submit_step4.html', data=session['submit_data'])
        
        file = request.files['file']
        if file.filename == '':
            flash('请选择要上传的文件！')
            return render_template('author/submit_step4.html', data=session['submit_data'])
        
        if not allowed_file(file.filename):
            flash('仅支持doc/docx/pdf格式的文件！')
            return render_template('author/submit_step4.html', data=session['submit_data'])
        
        # 保存文件
        filename = secure_filename(f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        session['submit_data']['file_path'] = file_path
        session.modified = True
        
        return redirect(url_for('author_submit_confirm'))
    
    return render_template('author/submit_step4.html', data=session['submit_data'])

# 投稿步骤5：确认投稿
@app.route('/author/submit/confirm', methods=['GET', 'POST'])
@login_required
def author_submit_confirm():
    if current_user.role != 'author':
        flash('无权限！')
        return redirect(url_for('login'))
    
    if 'submit_data' not in session:
        session['submit_data'] = {
            'title': '',
            'author_name': '',
            'keywords': '',
            'file_path': ''
        }
    data = session['submit_data']
    
    # 校验必填字段
    required_fields = {
        'title': '文章题目',
        'author_name': '作者姓名',
        'keywords': '关键词',
        'file_path': '稿件文件'
    }
    missing_fields = []
    for field, name in required_fields.items():
        if not data.get(field, '').strip():
            missing_fields.append(name)
    
    if missing_fields:
        flash(f'请先填写/上传完整：{"/".join(missing_fields)}！')
        if 'title' in missing_fields:
            return redirect(url_for('author_submit_step1'))
        elif 'author_name' in missing_fields:
            return redirect(url_for('author_submit_step2'))
        elif 'keywords' in missing_fields:
            return redirect(url_for('author_submit_step3'))
        elif 'file_path' in missing_fields:
            return redirect(url_for('author_submit_step4'))
    
    if request.method == 'POST':
        # 保存稿件
        manuscript = Manuscript(
            title=data.get('title', '').strip(),
            author_name=data.get('author_name', '').strip(),
            keywords=data.get('keywords', '').strip(),
            file_path=data.get('file_path', '').strip(),
            author_id=current_user.id
        )
        db.session.add(manuscript)
        db.session.commit()
        
        # 清空暂存数据
        session.pop('submit_data')
        flash('投稿成功！已进入待分配审核阶段')
        return redirect(url_for('author_papers'))
    
    return render_template('author/submit_confirm.html', data=data)

# 作者稿件状态查询（待审核/拒审/审核通过）
@app.route('/author/papers')
@login_required
def author_papers():
    if current_user.role != 'author':
        flash('无权限！')
        return redirect(url_for('login'))
    
    # 所有稿件
    all_papers = Manuscript.query.filter_by(author_id=current_user.id).all()
    
    # 待处理稿件（非历史：待分配/待评审/专家拒审/审核通过）
    pending_papers = [p for p in all_papers if p.status in ['pending_assign', 'pending_review', 'rejected_review', 'reviewed']]
    # 历史稿件（编辑已决定：退稿/录用）
    history_papers = [p for p in all_papers if p.status in ['rejected', 'accepted', 'published']]
    
    # 为每个稿件补充评审意见
    for paper in all_papers:
        paper.review_info = Review.query.filter_by(manuscript_id=paper.id).first()
    
    return render_template('author/papers.html', 
                           pending_papers=pending_papers,
                           history_papers=history_papers)

# 作者个人资料
@app.route('/author/profile', methods=['GET', 'POST'])
@login_required
def author_profile():
    if current_user.role != 'author':
        flash('无权限！')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        # 修改个人资料
        if 'update_info' in request.form:
            current_user.name = request.form['name'].strip()
            current_user.phone = request.form['phone'].strip()
            current_user.email = request.form['email'].strip()
            db.session.commit()
            flash('个人资料修改成功！')
        # 修改密码
        elif 'update_pwd' in request.form:
            old_pwd = request.form['old_pwd']
            new_pwd = request.form['new_pwd']
            confirm_pwd = request.form['confirm_pwd']
            
            if not current_user.check_password(old_pwd):
                flash('原密码错误！')
            elif new_pwd != confirm_pwd:
                flash('两次密码不一致！')
            else:
                current_user.set_password(new_pwd)
                db.session.commit()
                flash('密码修改成功！请重新登录')
                return redirect(url_for('logout'))
    
    return render_template('author/profile.html', user=current_user)

# -------------------------- 编辑模块 --------------------------
@app.route('/editor/index', methods=['GET', 'POST'])
@login_required
def editor_index():
    if current_user.role != 'editor':
        flash('无权限！')
        return redirect(url_for('login'))
    
    # 分配稿件
    if request.method == 'POST' and 'assign' in request.form:
        paper_id = request.form['paper_id']
        expert_id = request.form['expert_id']
        paper = Manuscript.query.get(paper_id)
        if paper:
            paper.expert_id = expert_id
            paper.status = 'pending_review'
            db.session.commit()
            flash('稿件分配成功！')
    
    # 重新分配拒审稿件
    if request.method == 'POST' and 'reassign' in request.form:
        paper_id = request.form['paper_id']
        expert_id = request.form['expert_id']
        paper = Manuscript.query.get(paper_id)
        if paper:
            paper.expert_id = expert_id
            paper.status = 'pending_review'
            # 清空拒审原因
            review = Review.query.filter_by(manuscript_id=paper.id).first()
            if review:
                review.reject_reason = None
            db.session.commit()
            flash('稿件重新分配成功！')
    
    # 编辑退稿/录用
    if request.method == 'POST' and 'decision' in request.form:
        paper_id = request.form['paper_id']
        action = request.form['action']
        paper = Manuscript.query.get(paper_id)
        if paper:
            paper.status = 'accepted' if action == 'accept' else 'rejected'
            db.session.commit()
            flash(f'稿件已{"录用" if action == "accept" else "退稿"}！')
    
    # 数据查询
    pending_assign = Manuscript.query.filter_by(status='pending_assign').all()  # 待分配
    pending_review = Manuscript.query.filter_by(status='pending_review').all()  # 待评审
    rejected_review = Manuscript.query.filter_by(status='rejected_review').all()  # 专家拒审
    reviewed_papers = Manuscript.query.filter_by(status='reviewed').all()  # 审核通过
    experts = User.query.filter_by(role='expert').all()
    
    # 补充评审信息
    for paper in reviewed_papers + rejected_review:
        paper.review_info = Review.query.filter_by(manuscript_id=paper.id).first()
    
    return render_template('editor/index.html',
                           pending_assign=pending_assign,
                           pending_review=pending_review,
                           rejected_review=rejected_review,
                           reviewed_papers=reviewed_papers,
                           experts=experts)

# -------------------------- 专家模块 --------------------------
@app.route('/expert/index')
@login_required
def expert_index():
    if current_user.role != 'expert':
        flash('无权限！')
        return redirect(url_for('login'))
    
    # 分配给当前专家的待评审稿件
    papers = Manuscript.query.filter_by(expert_id=current_user.id, status='pending_review').all()
    return render_template('expert/index.html', papers=papers)

# 专家评审/拒审
@app.route('/expert/review/<int:paper_id>', methods=['GET', 'POST'])
@login_required
def expert_review(paper_id):
    if current_user.role != 'expert':
        flash('无权限！')
        return redirect(url_for('login'))
    
    paper = Manuscript.query.get(paper_id)
    if not paper or paper.expert_id != current_user.id:
        flash('无效稿件！')
        return redirect(url_for('expert_index'))
    
    if request.method == 'POST':
        # 拒审逻辑
        if request.form['action'] == 'reject':
            reject_reason = request.form['reject_reason'].strip()
            if not reject_reason:
                flash('拒审原因不能为空！')
                return render_template('expert/review.html', paper=paper)
            if len(reject_reason) > 200:
                flash('拒审原因不能超过200字！')
                return render_template('expert/review.html', paper=paper)
            
            # 保存拒审原因
            review = Review.query.filter_by(manuscript_id=paper.id).first()
            if not review:
                review = Review(manuscript_id=paper.id, score=0, opinion='', reject_reason=reject_reason)
                db.session.add(review)
            else:
                review.reject_reason = reject_reason
                review.score = 0
                review.opinion = ''
            
            # 专家拒审状态：rejected_review（区分编辑退稿rejected）
            paper.status = 'rejected_review'
            db.session.commit()
            flash('已提交拒审！稿件状态已更新为拒审')
            return redirect(url_for('expert_index'))
        
        # 评审逻辑
        elif request.form['action'] == 'review':
            score = request.form.get('score')
            if not score or not score.isdigit():
                flash('请输入有效的评分（0-100分）！')
                return render_template('expert/review.html', paper=paper)
            score = int(score)
            opinion = request.form['opinion'].strip()
            
            if score < 0 or score > 100:
                flash('评分必须在0-100之间！')
                return render_template('expert/review.html', paper=paper)
            if not opinion or len(opinion) > 200:
                flash('评审意见不能为空且不超过200字！')
                return render_template('expert/review.html', paper=paper)
            
            # 保存评审意见
            review = Review.query.filter_by(manuscript_id=paper.id).first()
            if not review:
                review = Review(manuscript_id=paper.id, score=score, opinion=opinion, reject_reason='')
                db.session.add(review)
            else:
                review.score = score
                review.opinion = opinion
                review.reject_reason = ''
            
            paper.status = 'reviewed'
            db.session.commit()
            flash('评审完成！')
            return redirect(url_for('expert_index'))
    
    return render_template('expert/review.html', paper=paper)

# -------------------------- 主编模块 --------------------------
@app.route('/chief/index')
@login_required
def chief_index():
    if current_user.role != 'chief':
        flash('无权限！')
        return redirect(url_for('login'))
    
    accepted_papers = Manuscript.query.filter_by(status='accepted').all()
    return render_template('chief/index.html', papers=accepted_papers)

@app.route('/chief/publish', methods=['GET', 'POST'])
@login_required
def chief_publish():
    if current_user.role != 'chief':
        flash('无权限！')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        for key, value in request.form.items():
            if key.startswith('sort_'):
                paper_id = int(key.split('_')[1])
                sort_num = int(value)
                paper = Manuscript.query.get(paper_id)
                paper.sort_num = sort_num
                paper.status = 'published'
                paper.publish_time = datetime.date.today()
        db.session.commit()
        flash('稿件发表成功！')
        return redirect(url_for('chief_published'))
    
    accepted_papers = Manuscript.query.filter_by(status='accepted').all()
    return render_template('chief/publish.html', papers=accepted_papers)

@app.route('/chief/published')
@login_required
def chief_published():
    if current_user.role != 'chief':
        flash('无权限！')
        return redirect(url_for('login'))
    
    published_papers = Manuscript.query.filter_by(status='published').order_by(Manuscript.sort_num).all()
    return render_template('chief/published.html', papers=published_papers)

# -------------------------- 通用下载接口 --------------------------
@app.route('/download/<int:paper_id>')
@login_required
def download(paper_id):
    paper = Manuscript.query.get(paper_id)
    if not paper:
        flash('稿件不存在！')
        return redirect(url_for(f'{current_user.role}_index'))
    
    # 权限验证
    has_permission = False
    if current_user.role == 'author':
        has_permission = (paper.author_id == current_user.id)
    elif current_user.role == 'editor':
        has_permission = True
    elif current_user.role == 'expert':
        has_permission = (paper.expert_id == current_user.id)
    elif current_user.role == 'chief':
        has_permission = (paper.status in ['accepted', 'published'])
    
    if not has_permission:
        flash('无权限下载该稿件！')
        return redirect(url_for(f'{current_user.role}_index'))
    
    return send_file(paper.file_path, as_attachment=True, download_name=os.path.basename(paper.file_path))

# -------------------------- 初始化测试数据 --------------------------
def init_test_data():
    """创建固定账号：编辑/专家/主编各一个，作者可注册"""
    if User.query.count() > 0:
        return
    
    # 编辑账号：editor/123456
    editor = User(username='editor', name='系统编辑', role='editor')
    editor.set_password('123456')
    # 专家账号：expert/123456
    expert = User(username='expert', name='系统专家', role='expert')
    expert.set_password('123456')
    # 主编账号：chief/123456
    chief = User(username='chief', name='系统主编', role='chief')
    chief.set_password('123456')
    
    db.session.add_all([editor, expert, chief])
    db.session.commit()
    print("固定账号创建成功：")
    print("编辑：editor/123456")
    print("专家：expert/123456")
    print("主编：chief/123456")
    print("作者账号可通过注册页面创建")

# -------------------------- 启动程序 --------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        init_test_data()
    app.run(debug=True, host='0.0.0.0', port=5000)