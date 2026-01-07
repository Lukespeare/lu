// ==================== 通用工具函数 ====================
// 显示提示信息
function showMessage(elementId, message, isError = false) {
    const el = document.getElementById(elementId);
    el.textContent = message;
    el.style.color = isError ? '#f44336' : '#4CAF50';
    el.classList.remove('hidden');
    setTimeout(() => {
        el.classList.add('hidden');
    }, 3000);
}

// ==================== 顾客点餐页面逻辑 ====================
// 切换订单类型
let currentOrderType = 'dinein';
function switchOrderType(type) {
    currentOrderType = type;
    // 切换按钮样式
    document.querySelectorAll('.order-type button').forEach(btn => {
        btn.classList.remove('active');
    });
    event.target.classList.add('active');
    // 切换表单显示
    document.getElementById('dinein-form').classList.toggle('hidden', type !== 'dinein');
    document.getElementById('takeout-form').classList.toggle('hidden', type !== 'takeout');
}

// 提交订单
function submitOrder() {
    const formData = new FormData();
    formData.append('order_type', currentOrderType);

    // 添加订单基础信息
    if (currentOrderType === 'dinein') {
        const tableNum = document.getElementById('table_num').value;
        const hasRoomFee = document.getElementById('has_room_fee').checked ? 1 : 0;
        if (!tableNum) {
            alert('请输入餐桌号');
            return;
        }
        formData.append('table_num', tableNum);
        formData.append('has_room_fee', hasRoomFee);
    } else {
        const takeoutTime = document.getElementById('takeout_time').value;
        const takeoutAddress = document.getElementById('takeout_address').value;
        const phone = document.getElementById('phone').value;
        if (!takeoutTime || !takeoutAddress || !phone) {
            alert('请填写完整外卖信息');
            return;
        }
        formData.append('takeout_time', takeoutTime);
        formData.append('takeout_address', takeoutAddress);
        formData.append('phone', phone);
    }

    // 添加菜品信息
    const quantityInputs = document.querySelectorAll('.quantity');
    let hasItem = false;
    quantityInputs.forEach(input => {
        const dishId = input.getAttribute('data-dish-id');
        const quantity = input.value;
        if (quantity > 0) {
            formData.append('dish_id', dishId);
            formData.append('quantity', quantity);
            hasItem = true;
        }
    });
    if (!hasItem) {
        alert('请选择至少一道菜品');
        return;
    }

    // 提交请求
    fetch('/submit_order', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        const resultDiv = document.getElementById('order-result');
        resultDiv.classList.remove('hidden');
        if (data.success) {
            resultDiv.textContent = data.order_info;
            // 重置表单
            document.getElementById('table_num').value = '';
            document.getElementById('has_room_fee').checked = false;
            document.getElementById('takeout_time').value = '';
            document.getElementById('takeout_address').value = '';
            document.getElementById('phone').value = '';
            quantityInputs.forEach(input => input.value = 0);
        } else {
            resultDiv.textContent = '提交失败：' + data.error;
        }
    })
    .catch(error => {
        alert('网络错误：' + error);
    });
}

// ==================== 管理员面板逻辑 ====================
// 添加菜品
function addDish() {
    const name = document.getElementById('dish_name').value;
    const price = parseFloat(document.getElementById('dish_price').value);
    const discount = parseFloat(document.getElementById('dish_discount').value);

    if (!name || isNaN(price) || price <= 0 || isNaN(discount) || discount <= 0 || discount > 1) {
        showMessage('dish-message', '请输入有效的菜品信息（折扣0-1之间）', true);
        return;
    }

    const formData = new FormData();
    formData.append('name', name);
    formData.append('price', price);
    formData.append('discount', discount);

    fetch('/admin/dish/add', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showMessage('dish-message', '菜品添加成功！');
            // 重置表单
            document.getElementById('dish_name').value = '';
            document.getElementById('dish_price').value = '';
            document.getElementById('dish_discount').value = 1.0;
            // 刷新菜品列表
            location.reload();
        } else {
            showMessage('dish-message', '添加失败：' + data.error, true);
        }
    })
    .catch(error => {
        showMessage('dish-message', '网络错误：' + error, true);
    });
}

// 修改菜品
function updateDish() {
    const dishId = document.getElementById('update_dish_id').value;
    const newName = document.getElementById('update_dish_name').value;
    const newPrice = document.getElementById('update_dish_price').value;
    const newDiscount = document.getElementById('update_dish_discount').value;

    if (!dishId) {
        showMessage('update-message', '请输入菜品ID', true);
        return;
    }

    const formData = new FormData();
    formData.append('dish_id', dishId);
    if (newName) formData.append('new_name', newName);
    if (newPrice) formData.append('new_price', parseFloat(newPrice));
    if (newDiscount) formData.append('new_discount', parseFloat(newDiscount));

    fetch('/admin/dish/update', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showMessage('update-message', '菜品修改成功！');
            // 重置表单
            document.getElementById('update_dish_id').value = '';
            document.getElementById('update_dish_name').value = '';
            document.getElementById('update_dish_price').value = '';
            document.getElementById('update_dish_discount').value = '';
            // 刷新菜品列表
            location.reload();
        } else {
            showMessage('update-message', '修改失败：' + data.error, true);
        }
    })
    .catch(error => {
        showMessage('update-message', '网络错误：' + error, true);
    });
}

// 删除菜品
function deleteDish() {
    const dishId = document.getElementById('delete_dish_id').value;
    if (!dishId) {
        showMessage('delete-message', '请输入菜品ID', true);
        return;
    }

    if (!confirm('确定要删除该菜品吗？')) {
        return;
    }

    const formData = new FormData();
    formData.append('dish_id', dishId);

    fetch('/admin/dish/delete', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showMessage('delete-message', '菜品删除成功！');
            // 重置表单
            document.getElementById('delete_dish_id').value = '';
            // 刷新菜品列表
            location.reload();
        } else {
            showMessage('delete-message', '删除失败：' + data.error, true);
        }
    })
    .catch(error => {
        showMessage('delete-message', '网络错误：' + error, true);
    });
}

// 搜索订单
function searchOrder() {
    const searchType = document.getElementById('search_type').value;
    const keyword = document.getElementById('search_keyword').value;

    if (!keyword) {
        showMessage('order-message', '请输入搜索关键词', true);
        return;
    }

    const formData = new FormData();
    formData.append('search_type', searchType);
    formData.append('keyword', keyword);

    fetch('/admin/order/search', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        const resultDiv = document.getElementById('order-result');
        resultDiv.classList.remove('hidden');
        if (data.success) {
            if (data.orders.length === 0) {
                resultDiv.textContent = '未找到相关订单';
                return;
            }
            let orderText = '';
            data.orders.forEach((order, index) => {
                orderText += `\n===== 订单 ${index+1} =====\n${order.info}\n`;
            });
            resultDiv.textContent = orderText;
        } else {
            resultDiv.textContent = '搜索失败：' + data.error;
        }
    })
    .catch(error => {
        showMessage('order-message', '网络错误：' + error, true);
    });
}

// 删除订单
function deleteOrder() {
    const orderNo = document.getElementById('delete_order_no').value;
    if (!orderNo) {
        showMessage('delete-order-message', '请输入订单编号', true);
        return;
    }

    if (!confirm('确定要删除该订单吗？')) {
        return;
    }

    const formData = new FormData();
    formData.append('order_no', orderNo);

    fetch('/admin/order/delete', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showMessage('delete-order-message', '订单删除成功！');
            document.getElementById('delete_order_no').value = '';
        } else {
            showMessage('delete-order-message', '删除失败：' + data.error, true);
        }
    })
    .catch(error => {
        showMessage('delete-order-message', '网络错误：' + error, true);
    });
}