// 仓储系统通用脚本

// API 基础路径
const API_BASE_URL = 'api';

// 当前登录用户及仓库上下文
let currentUser = null;
let userWarehouses = [];
let currentWarehouse = null;

// 统一 HTML 转义，避免将用户可控内容注入 innerHTML
function escapeHTML(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

window.escapeHTML = escapeHTML;

// 页面加载后初始化通用能力
document.addEventListener('DOMContentLoaded', function() {
    initCommon();
});

// 初始化：鉴权、导航交互、仓库切换、用户菜单、退出登录
function initCommon() {
    checkLoginStatus();
    setupSidebarToggle();
    setupWarehouseSelector();
    setupUserMenu();
    setupLogout();
}

// 读取并选择最佳认证信息（本页存储优先，其次父页面）
function getStoredAuth() {
    const readAuth = (storage) => ({
        userInfo: storage.getItem('user'),
        expiry: storage.getItem('token_expiry')
    });

    const isComplete = (auth) => !!auth.userInfo;
    const isExpired = (auth) => !!(auth.expiry && new Date() >= new Date(auth.expiry));

    const pickBest = (localAuth, sessionAuth) => {
        if (isComplete(localAuth) && !isExpired(localAuth)) return localAuth;
        if (isComplete(sessionAuth) && !isExpired(sessionAuth)) return sessionAuth;
        if (isComplete(localAuth)) return localAuth;
        if (isComplete(sessionAuth)) return sessionAuth;
        return null;
    };

    const localAuth = readAuth(localStorage);
    const sessionAuth = readAuth(sessionStorage);
    const ownAuth = pickBest(localAuth, sessionAuth);
    if (ownAuth) return ownAuth;

    // 嵌入模式下，兜底从父页面读取会话
    if (isEmbeddedMode()) {
        try {
            if (window.top && window.top !== window && window.top.location.origin === window.location.origin) {
                const topLocalAuth = readAuth(window.top.localStorage);
                const topSessionAuth = readAuth(window.top.sessionStorage);
                const topAuth = pickBest(topLocalAuth, topSessionAuth);
                if (topAuth) return topAuth;
            }
        } catch (error) {
            // 忽略跨上下文访问异常
        }
    }

    return { userInfo: null, expiry: null };
}

// 校验登录状态并初始化页面上下文
function checkLoginStatus() {
    // 强制清理历史 token 落地，统一改为 HttpOnly Cookie 会话
    localStorage.removeItem('access_token');
    localStorage.removeItem('token');
    sessionStorage.removeItem('access_token');

    const { userInfo, expiry } = getStoredAuth();

    if (!userInfo) {
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

        updateUserDisplay();

        if (currentUser.current_warehouse_id) {
            currentWarehouse = userWarehouses.find(w => w.id === currentUser.current_warehouse_id);
            updateWarehouseDisplay();
        }

        // 非 admin 隐藏仓库管理菜单
        if (getCurrentUserRole() !== 'admin') {
            const warehouseNavLinks = document.querySelectorAll('a[href="warehouse.html"]');
            warehouseNavLinks.forEach(link => {
                link.style.display = 'none';
            });
        }

        // 非 admin 隐藏申请管理菜单（仅管理员可见）
        if (getCurrentUserRole() !== 'admin') {
            const requestNavLinks = document.querySelectorAll('a[href="request-admin.html"]');
            requestNavLinks.forEach(link => {
                link.style.display = 'none';
            });
        }

        // 让业务页面执行自身初始化
        if (typeof pageInit === 'function') {
            pageInit();
        }
    } catch (error) {
        console.error('解析用户信息失败:', error);
        logout();
    }
}

// 更新右上角用户显示
function updateUserDisplay() {
    if (currentUser) {
        const initials = currentUser.full_name ? currentUser.full_name.charAt(0).toUpperCase() : 'U';
        document.getElementById('userInitials').textContent = initials;
        document.getElementById('userName').textContent = currentUser.full_name || currentUser.username;
    }
}

// 更新当前仓库显示
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

// 移动端侧边栏收起/展开
function setupSidebarToggle() {
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function() {
            sidebar.classList.toggle('hidden');
        });
    }
}

// 仓库选择器交互
function setupWarehouseSelector() {
    const selector = document.getElementById('warehouseSelector');
    const dropdown = document.getElementById('warehouseDropdown');
    const list = document.getElementById('warehouseList');

    if (!selector || !dropdown || !list) return;

    selector.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.classList.toggle('hidden');
        updateWarehouseList();
    });

    document.addEventListener('click', function() {
        dropdown.classList.add('hidden');
    });

    dropdown.addEventListener('click', function(e) {
        e.stopPropagation();
    });
}

// 刷新仓库下拉列表
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
                <span>${escapeHTML(warehouse.name)}</span>
                ${warehouse.is_default ? '<span class="text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded">默认</span>' : ''}
            </div>
            <div class="text-xs text-gray-500">${escapeHTML(warehouse.code)}</div>
        `;

        item.addEventListener('click', function() {
            switchWarehouse(warehouse.id);
        });

        list.appendChild(item);
    });
}

// 切换当前仓库
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

        currentWarehouse = userWarehouses.find(w => w.id === warehouseId);
        currentUser.current_warehouse_id = warehouseId;

        if (localStorage.getItem('user')) {
            localStorage.setItem('user', JSON.stringify(currentUser));
        }
        if (sessionStorage.getItem('user')) {
            sessionStorage.setItem('user', JSON.stringify(currentUser));
        }

        updateWarehouseDisplay();
        updateWarehouseList();
        document.getElementById('warehouseDropdown').classList.add('hidden');

        showToast('仓库切换成功', 'success');

        // 通知各业务页面刷新数据
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

// 用户菜单交互
function setupUserMenu() {
    const userMenu = document.getElementById('userMenu');
    const dropdown = document.getElementById('userDropdown');

    if (!userMenu || !dropdown) return;

    userMenu.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdown.classList.toggle('hidden');
    });

    document.addEventListener('click', function() {
        dropdown.classList.add('hidden');
    });

    dropdown.addEventListener('click', function(e) {
        e.stopPropagation();
    });
}

// 绑定退出登录按钮
function setupLogout() {
    const logoutButton = document.getElementById('logoutButton');

    if (logoutButton) {
        logoutButton.addEventListener('click', function(e) {
            e.preventDefault();
            logout();
        });
    }
}

// 是否为嵌入模式（iframe / embedded=1）
function isEmbeddedMode() {
    const params = new URLSearchParams(window.location.search);
    return window.top !== window.self || params.get('embedded') === '1';
}



// 退出登录并回到登录页
function logout() {
    // 最佳努力通知后端清理 HttpOnly 会话 Cookie
    fetch(`${API_BASE_URL}/logout`, { method: 'POST', credentials: 'same-origin' })
        .catch(() => {});

    localStorage.removeItem('access_token');
    localStorage.removeItem('auth_mode');
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    localStorage.removeItem('token_expiry');
    sessionStorage.removeItem('access_token');
    sessionStorage.removeItem('auth_mode');
    sessionStorage.removeItem('user');
    sessionStorage.removeItem('token_expiry');

    window.location.href = 'index.html';
}

// 获取带认证头的请求头
function getHeaders() {
    return {
        'Content-Type': 'application/json'
    };
}

// 右上角 Toast 提示
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 px-6 py-3 rounded-md shadow-lg z-50 transform transition-all duration-300 translate-x-full opacity-0`;

    const icon = document.createElement('i');
    icon.className = 'mr-2';
    const text = document.createElement('span');
    text.textContent = String(message ?? '');

    if (type === 'success') {
        toast.classList.add('bg-green-500', 'text-white');
        icon.classList.add('fa', 'fa-check-circle');
    } else if (type === 'error') {
        toast.classList.add('bg-red-500', 'text-white');
        icon.classList.add('fa', 'fa-times-circle');
    } else if (type === 'warning') {
        toast.classList.add('bg-yellow-500', 'text-white');
        icon.classList.add('fa', 'fa-exclamation-triangle');
    } else {
        toast.classList.add('bg-blue-500', 'text-white');
        icon.classList.add('fa', 'fa-info-circle');
    }

    toast.appendChild(icon);
    toast.appendChild(text);
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    }, 100);

    setTimeout(() => {
        toast.classList.add('translate-x-full', 'opacity-0');
        setTimeout(() => {
            document.body.removeChild(toast);
        }, 300);
    }, 3000);
}

// 当前用户角色
function getCurrentUserRole() {
    return currentUser ? currentUser.role : 'operator';
}

// 根据仓库 ID 获取仓库名称
function getWarehouseName(warehouseId) {
    const warehouse = userWarehouses.find(w => w.id === warehouseId);
    return warehouse ? warehouse.name : '未知仓库';
}

// 日期时间格式化
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

// 日期格式化
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    });
}

// 货币格式化
function formatCurrency(amount) {
    return new Intl.NumberFormat('zh-CN', {
        style: 'currency',
        currency: 'CNY',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(amount);
}

// 生成随机 ID
function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).substr(2);
}

// 表单字段校验
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

// 防抖
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

// 节流
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
