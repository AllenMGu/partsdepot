// 多仓库管理系统通用脚本

// API基础URL
const API_BASE_URL = 'api';

// 当前用户信息
let currentUser = null;
let userWarehouses = [];
let currentWarehouse = null;

// 页面加载完成后执行
document.addEventListener('DOMContentLoaded', function() {
    // 初始化通用功能
    initCommon();
});

// 初始化通用功能
function initCommon() {
    // 检查登录状态
    checkLoginStatus();
    
    // 设置侧边栏切换
    setupSidebarToggle();
    
    // 设置仓库选择器
    setupWarehouseSelector();
    
    // 设置用户菜单
    setupUserMenu();
    
    // 设置退出登录
    setupLogout();
}

// 检查登录状态
function checkLoginStatus() {
    let token = localStorage.getItem('access_token');
    const userInfo = localStorage.getItem('user');
    const expiry = localStorage.getItem('token_expiry');

    if (!token) {
        const legacyToken = localStorage.getItem('token');
        if (legacyToken) {
            localStorage.setItem('access_token', legacyToken);
            localStorage.removeItem('token');
            token = legacyToken;
        }
    }
    
    if (!token || !userInfo) {
        // 未登录，跳转到登录页
        window.location.href = 'index.html';
        return;
    }

    if (expiry && new Date() >= new Date(expiry)) {
        logout();
        return;
    }
    
    try {
        currentUser = JSON.parse(userInfo);
        userWarehouses = currentUser.warehouses || [];

        // 更新用户信息显示
        updateUserDisplay();

        // 设置当前仓库
        if (currentUser.current_warehouse_id) {
            currentWarehouse = userWarehouses.find(w => w.id === currentUser.current_warehouse_id);
            updateWarehouseDisplay();
        }

        // 根据用户角色隐藏或显示仓库管理导航链接
        if (getCurrentUserRole() !== 'admin') {
            const warehouseNavLinks = document.querySelectorAll('a[href="warehouse.html"]');
            warehouseNavLinks.forEach(link => {
                link.style.display = 'none';
            });
        }

        // 页面特定初始化
        if (typeof pageInit === 'function') {
            pageInit();
        }
    } catch (error) {
        console.error('解析用户信息失败:', error);
        logout();
    }
}

// 更新用户信息显示
function updateUserDisplay() {
    if (currentUser) {
        // 更新用户头像首字母
        const initials = currentUser.full_name ? currentUser.full_name.charAt(0).toUpperCase() : 'U';
        document.getElementById('userInitials').textContent = initials;
        
        // 更新用户名
        document.getElementById('userName').textContent = currentUser.full_name || currentUser.username;
    }
}

// 更新仓库显示
function updateWarehouseDisplay() {
    if (currentWarehouse) {
        document.getElementById('currentWarehouse').textContent = currentWarehouse.name;
    } else if (userWarehouses.length > 0) {
        currentWarehouse = userWarehouses[0];
        document.getElementById('currentWarehouse').textContent = currentWarehouse.name;
    } else {
        document.getElementById('currentWarehouse').textContent = '无可用仓库';
    }
}

// 设置侧边栏切换
function setupSidebarToggle() {
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');
    
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function() {
            sidebar.classList.toggle('hidden');
        });
    }
}

// 设置仓库选择器
function setupWarehouseSelector() {
    const selector = document.getElementById('warehouseSelector');
    const dropdown = document.getElementById('warehouseDropdown');
    const list = document.getElementById('warehouseList');
    
    if (!selector || !dropdown || !list) return;
    
    // 点击选择器切换下拉框
    selector.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.classList.toggle('hidden');
        updateWarehouseList();
    });
    
    // 点击其他地方关闭下拉框
    document.addEventListener('click', function() {
        dropdown.classList.add('hidden');
    });
    
    // 阻止下拉框内点击事件冒泡
    dropdown.addEventListener('click', function(e) {
        e.stopPropagation();
    });
}

// 更新仓库列表
function updateWarehouseList() {
    const list = document.getElementById('warehouseList');
    
    if (!list) return;
    
    list.innerHTML = '';
    
    if (userWarehouses.length === 0) {
        list.innerHTML = `
            <div class="px-4 py-2 text-sm text-gray-500">
                无可用仓库
            </div>
        `;
        return;
    }
    
    userWarehouses.forEach(warehouse => {
        const item = document.createElement('div');
        item.className = `px-4 py-2 text-sm cursor-pointer hover:bg-gray-100 ${currentWarehouse && currentWarehouse.id === warehouse.id ? 'bg-primary text-white' : 'text-gray-700'}`;
        item.innerHTML = `
            <div class="flex items-center justify-between">
                <span>${warehouse.name}</span>
                ${warehouse.is_default ? '<span class="text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded">默认</span>' : ''}
            </div>
            <div class="text-xs text-gray-500">${warehouse.code}</div>
        `;
        
        item.addEventListener('click', function() {
            switchWarehouse(warehouse.id);
        });
        
        list.appendChild(item);
    });
}

// 切换仓库
async function switchWarehouse(warehouseId) {
    try {
        const params = new URLSearchParams({ warehouse_id: warehouseId });
        const response = await fetch(`${API_BASE_URL}/users/${currentUser.id}/switch-warehouse?${params.toString()}`, {
            method: 'POST',
            headers: getHeaders()
        });
        
        if (!response.ok) {
            throw new Error('切换仓库失败');
        }
        
        const result = await response.json();
        
        // 更新当前仓库
        currentWarehouse = userWarehouses.find(w => w.id === warehouseId);
        currentUser.current_warehouse_id = warehouseId;
        
        // 更新本地存储
        localStorage.setItem('user', JSON.stringify(currentUser));
        
        // 更新显示
        updateWarehouseDisplay();
        updateWarehouseList();
        
        // 关闭下拉框
        document.getElementById('warehouseDropdown').classList.add('hidden');
        
        // 显示提示
        showToast('仓库切换成功', 'success');
        
        // 刷新页面数据
        if (typeof loadLocations === 'function') loadLocations();
        if (typeof loadGoods === 'function') loadGoods();
        if (typeof loadStock === 'function') loadStock();
        if (typeof loadInboundOrders === 'function') loadInboundOrders();
        if (typeof loadOutboundOrders === 'function') loadOutboundOrders();
        if (typeof loadCheckRecords === 'function') loadCheckRecords();
        
    } catch (error) {
        console.error('切换仓库失败:', error);
        showToast('切换仓库失败', 'error');
    }
}

// 设置用户菜单
function setupUserMenu() {
    const userMenu = document.getElementById('userMenu');
    const dropdown = document.getElementById('userDropdown');
    
    if (!userMenu || !dropdown) return;
    
    // 点击用户菜单切换下拉框
    userMenu.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.classList.toggle('hidden');
    });
    
    // 点击其他地方关闭下拉框
    document.addEventListener('click', function() {
        dropdown.classList.add('hidden');
    });
    
    // 阻止下拉框内点击事件冒泡
    dropdown.addEventListener('click', function(e) {
        e.stopPropagation();
    });
}

// 设置退出登录
function setupLogout() {
    const logoutButton = document.getElementById('logoutButton');
    
    if (logoutButton) {
        logoutButton.addEventListener('click', function(e) {
            e.preventDefault();
            logout();
        });
    }
}

// 退出登录
function logout() {
    // 清除本地存储
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    localStorage.removeItem('token_expiry');
    
    // 跳转到登录页
    window.location.href = 'index.html';
}

// 获取请求头
function getHeaders() {
    const token = localStorage.getItem('access_token') || localStorage.getItem('token');
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };
}

// 显示提示消息
function showToast(message, type = 'info') {
    // 创建提示元素
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 px-6 py-3 rounded-md shadow-lg z-50 transform transition-all duration-300 translate-x-full opacity-0`;
    
    // 根据类型设置样式
    if (type === 'success') {
        toast.classList.add('bg-green-500', 'text-white');
        toast.innerHTML = `<i class="fa fa-check-circle mr-2"></i>${message}`;
    } else if (type === 'error') {
        toast.classList.add('bg-red-500', 'text-white');
        toast.innerHTML = `<i class="fa fa-times-circle mr-2"></i>${message}`;
    } else if (type === 'warning') {
        toast.classList.add('bg-yellow-500', 'text-white');
        toast.innerHTML = `<i class="fa fa-exclamation-triangle mr-2"></i>${message}`;
    } else {
        toast.classList.add('bg-blue-500', 'text-white');
        toast.innerHTML = `<i class="fa fa-info-circle mr-2"></i>${message}`;
    }
    
    // 添加到页面
    document.body.appendChild(toast);
    
    // 显示提示
    setTimeout(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    }, 100);
    
    // 自动隐藏
    setTimeout(() => {
        toast.classList.add('translate-x-full', 'opacity-0');
        
        // 移除元素
        setTimeout(() => {
            document.body.removeChild(toast);
        }, 300);
    }, 3000);
}

// 获取当前用户角色
function getCurrentUserRole() {
    return currentUser ? currentUser.role : 'operator';
}

// 获取仓库名称
function getWarehouseName(warehouseId) {
    const warehouse = userWarehouses.find(w => w.id === warehouseId);
    return warehouse ? warehouse.name : '未知仓库';
}

// 格式化日期时间
function formatDateTime(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// 格式化日期
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    });
}

// 格式化金额
function formatCurrency(amount) {
    return new Intl.NumberFormat('zh-CN', {
        style: 'currency',
        currency: 'CNY',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(amount);
}

// 生成随机ID
function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).substr(2);
}

// 验证表单字段
function validateField(value, type = 'required', min = null, max = null) {
    if (type === 'required' && !value) {
        return false;
    }
    
    if (type === 'number' && value) {
        const num = parseFloat(value);
        if (isNaN(num)) return false;
        if (min !== null && num < min) return false;
        if (max !== null && num > max) return false;
    }
    
    if (type === 'length' && value) {
        if (min !== null && value.length < min) return false;
        if (max !== null && value.length > max) return false;
    }
    
    return true;
}

// 防抖函数
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// 节流函数
function throttle(func, limit) {
    let inThrottle;
    return function() {
        const args = arguments;
        const context = this;
        if (!inThrottle) {
            func.apply(context, args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}