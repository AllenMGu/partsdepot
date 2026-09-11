// 统一的工具函数文件

// API基础URL
const API_BASE_URL = 'api';

// 全局变量
let currentUser = null;
let accessToken = null;
let warehouses = [];
let currentWarehouseId = null;

// ==================== 认证相关 ====================

// 检查认证状态
function checkAuth() {
    accessToken = localStorage.getItem('access_token');
    const userStr = localStorage.getItem('user');
    
    if (!accessToken || !userStr) {
        return false;
    }
    
    try {
        currentUser = JSON.parse(userStr);
        return true;
    } catch (e) {
        console.error('解析用户信息失败:', e);
        return false;
    }
}

// 获取认证头
function getAuthHeaders() {
    return {
        'Authorization': `Bearer ${accessToken}`
    };
}

// 获取完整的请求头
function getHeaders(contentType = 'application/json') {
    const headers = {
        ...getAuthHeaders(),
        'Accept': 'application/json'
    };
    
    if (contentType) {
        headers['Content-Type'] = contentType;
    }
    
    return headers;
}

// 退出登录
function logout() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    localStorage.removeItem('token_expiry');
    window.location.href = 'index.html';
}

// ==================== API调用相关 ====================

// 通用GET请求
async function apiGet(endpoint, params = {}) {
    try {
        // 构建查询字符串
        const queryString = Object.keys(params).length > 0 
            ? '?' + new URLSearchParams(params).toString() 
            : '';
        
        const response = await fetch(`${API_BASE_URL}${endpoint}${queryString}`, {
            method: 'GET',
            headers: getHeaders()
        });
        
        if (!response.ok) {
            handleApiError(response);
        }
        
        return await response.json();
    } catch (error) {
        handleFetchError(error);
        throw error;
    }
}

// 通用POST请求
async function apiPost(endpoint, data = {}, contentType = 'application/json') {
    try {
        let body;
        let headers = getHeaders(contentType);
        
        if (contentType === 'application/json') {
            body = JSON.stringify(data);
        } else if (contentType === 'application/x-www-form-urlencoded') {
            body = new URLSearchParams(data).toString();
        } else if (contentType === 'multipart/form-data') {
            body = data;
            delete headers['Content-Type']; // 让浏览器自动设置
        }
        
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            method: 'POST',
            headers: headers,
            body: body
        });
        
        if (!response.ok) {
            handleApiError(response);
        }
        
        // 检查响应是否为空
        const contentTypeHeader = response.headers.get('content-type');
        if (contentTypeHeader && contentTypeHeader.includes('application/json')) {
            return await response.json();
        }
        
        return { success: true };
    } catch (error) {
        handleFetchError(error);
        throw error;
    }
}

// 通用PUT请求
async function apiPut(endpoint, data = {}) {
    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            method: 'PUT',
            headers: getHeaders(),
            body: JSON.stringify(data)
        });
        
        if (!response.ok) {
            handleApiError(response);
        }
        
        return await response.json();
    } catch (error) {
        handleFetchError(error);
        throw error;
    }
}

// 通用DELETE请求
async function apiDelete(endpoint) {
    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            method: 'DELETE',
            headers: getHeaders()
        });
        
        if (!response.ok) {
            handleApiError(response);
        }
        
        return { success: true };
    } catch (error) {
        handleFetchError(error);
        throw error;
    }
}

// 处理API错误
async function handleApiError(response) {
    try {
        const errorData = await response.json();
        if (errorData.detail) {
            throw new Error(errorData.detail);
        } else if (errorData.message) {
            throw new Error(errorData.message);
        } else {
            throw new Error(`API错误: ${response.status} ${response.statusText}`);
        }
    } catch (error) {
        if (error instanceof SyntaxError) {
            throw new Error(`API错误: ${response.status} ${response.statusText}`);
        }
        throw error;
    }
}

// 处理Fetch错误
function handleFetchError(error) {
    console.error('API请求失败:', error);
    
    if (error.message.includes('Failed to fetch')) {
        throw new Error('网络连接失败，请检查您的网络');
    }
    
    throw error;
}

// ==================== 仓库相关 ====================

// 加载用户仓库列表
async function loadUserWarehouses() {
    try {
        warehouses = await apiGet(`/users/${currentUser.id}/warehouses`);
        return warehouses;
    } catch (error) {
        console.error('加载仓库列表失败:', error);
        throw error;
    }
}

// 切换当前仓库
async function switchUserWarehouse(warehouseId) {
    try {
        const response = await apiPost(`/users/${currentUser.id}/switch-warehouse`, { 
            warehouse_id: warehouseId 
        });
        
        // 更新全局变量
        currentWarehouseId = warehouseId;
        
        // 更新本地存储的用户信息
        currentUser.current_warehouse_id = warehouseId;
        currentUser.current_warehouse_name = response.current_warehouse_name;
        localStorage.setItem('user', JSON.stringify(currentUser));
        
        return response;
    } catch (error) {
        console.error('切换仓库失败:', error);
        throw error;
    }
}

// 获取当前仓库信息
function getCurrentWarehouse() {
    if (!warehouses.length || !currentWarehouseId) {
        return null;
    }
    
    return warehouses.find(w => w.id === currentWarehouseId) || warehouses[0];
}

// ==================== UI相关 ====================

// 显示加载状态
function showLoading(element) {
    const originalContent = element.innerHTML;
    element.innerHTML = '<span class="loading mr-2"></span>加载中...';
    element.disabled = true;
    
    return function hideLoading() {
        element.innerHTML = originalContent;
        element.disabled = false;
    };
}

// 显示消息提示
function showMessage(message, type = 'info', duration = 3000) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `alert alert-${type} fixed top-4 right-4 z-50 max-w-md`;
    messageDiv.innerHTML = `
        <div class="flex items-center">
            <i class="fa fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : type === 'warning' ? 'exclamation-triangle' : 'info-circle'} mr-2"></i>
            <span>${message}</span>
        </div>
    `;
    
    document.body.appendChild(messageDiv);
    
    // 添加动画
    messageDiv.style.opacity = '0';
    messageDiv.style.transform = 'translateY(-20px)';
    messageDiv.style.transition = 'all 0.3s ease';
    
    setTimeout(() => {
        messageDiv.style.opacity = '1';
        messageDiv.style.transform = 'translateY(0)';
    }, 100);
    
    // 自动隐藏
    setTimeout(() => {
        messageDiv.style.opacity = '0';
        messageDiv.style.transform = 'translateY(-20px)';
        
        setTimeout(() => {
            if (messageDiv.parentNode) {
                messageDiv.parentNode.removeChild(messageDiv);
            }
        }, 300);
    }, duration);
    
    return messageDiv;
}

// 显示成功消息
function showSuccess(message, duration = 3000) {
    return showMessage(message, 'success', duration);
}

// 显示错误消息
function showError(message, duration = 5000) {
    return showMessage(message, 'error', duration);
}

// 显示警告消息
function showWarning(message, duration = 4000) {
    return showMessage(message, 'warning', duration);
}

// 显示确认对话框
function showConfirm(message, onConfirm, onCancel) {
    const modal = document.createElement('div');
    modal.className = 'modal show';
    modal.innerHTML = `
        <div class="modal-content max-w-md">
            <div class="modal-header">
                <h3 class="text-lg font-semibold text-gray-800">确认操作</h3>
                <button class="text-gray-500 hover:text-gray-700 focus:outline-none" onclick="this.closest('.modal').remove()">
                    <i class="fa fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <p class="text-gray-700">${message}</p>
            </div>
            <div class="modal-footer">
                <button class="btn-secondary" onclick="this.closest('.modal').remove(); if (typeof onCancel === 'function') onCancel();">
                    取消
                </button>
                <button class="btn-danger" onclick="this.closest('.modal').remove(); if (typeof onConfirm === 'function') onConfirm();">
                    确认
                </button>
            </div>
        </div>
    `;
    
    document.body.appendChild(modal);
    
    // 阻止事件冒泡
    modal.querySelector('.modal-content').addEventListener('click', e => {
        e.stopPropagation();
    });
    
    // 点击背景关闭
    modal.addEventListener('click', e => {
        if (e.target === modal) {
            modal.remove();
            if (typeof onCancel === 'function') onCancel();
        }
    });
    
    return modal;
}

// 格式化日期时间
function formatDateTime(date) {
    if (!date) return '';
    
    const d = new Date(date);
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const hours = String(d.getHours()).padStart(2, '0');
    const minutes = String(d.getMinutes()).padStart(2, '0');
    
    return `${year}-${month}-${day} ${hours}:${minutes}`;
}

// 格式化数字（保留2位小数）
function formatNumber(num, decimals = 2) {
    if (num === null || num === undefined) return '0';
    return parseFloat(num).toFixed(decimals);
}

// ==================== 初始化函数 ====================

// 页面初始化（用于需要认证的页面）
async function initAuthenticatedPage() {
    // 检查登录状态
    if (!checkAuth()) {
        window.location.href = 'index.html';
        return false;
    }
    
    // 初始化用户信息
    initUserInfo();
    
    return true;
}

// 初始化用户信息显示
function initUserInfo() {
    const userNameEl = document.getElementById('userName');
    const userInitialsEl = document.getElementById('userInitials');
    const welcomeUserEl = document.getElementById('welcomeUser');
    
    if (userNameEl) {
        userNameEl.textContent = currentUser.full_name || currentUser.username;
    }
    
    if (userInitialsEl) {
        userInitialsEl.textContent = (currentUser.full_name || currentUser.username).charAt(0).toUpperCase();
    }
    
    if (welcomeUserEl) {
        welcomeUserEl.textContent = currentUser.full_name || currentUser.username;
    }
    
    // 设置当前仓库ID
    if (currentUser.current_warehouse_id) {
        currentWarehouseId = currentUser.current_warehouse_id;
    }
}

// 初始化侧边栏
function initSidebar() {
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');
    
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', () => {
            sidebar.classList.toggle('hidden');
        });
    }
}

// 初始化用户菜单
function initUserMenu() {
    const userMenu = document.getElementById('userMenu');
    const userDropdown = document.getElementById('userDropdown');
    const logoutButton = document.getElementById('logoutButton');
    
    if (userMenu && userDropdown) {
        userMenu.addEventListener('click', (e) => {
            e.stopPropagation();
            userDropdown.classList.toggle('hidden');
            
            // 关闭其他下拉菜单
            document.querySelectorAll('.dropdown').forEach(dropdown => {
                if (dropdown !== userDropdown) {
                    dropdown.classList.add('hidden');
                }
            });
        });
    }
    
    if (logoutButton) {
        logoutButton.addEventListener('click', (e) => {
            e.preventDefault();
            logout();
        });
    }
    
    // 点击其他地方关闭下拉菜单
    document.addEventListener('click', () => {
        if (userDropdown) {
            userDropdown.classList.add('hidden');
        }
    });
}

// 初始化仓库选择器
async function initWarehouseSelector() {
    const warehouseSelector = document.getElementById('warehouseSelector');
    const warehouseDropdown = document.getElementById('warehouseDropdown');
    const warehouseList = document.getElementById('warehouseList');
    const currentWarehouseEl = document.getElementById('currentWarehouse');
    
    if (!warehouseSelector || !warehouseDropdown || !warehouseList || !currentWarehouseEl) {
        return;
    }
    
    try {
        // 加载仓库列表
        await loadUserWarehouses();
        
        if (warehouses.length > 0) {
            let selectedWarehouse = null;
            
            // 查找当前仓库
            if (currentWarehouseId) {
                selectedWarehouse = warehouses.find(w => w.id === currentWarehouseId);
            }
            
            // 如果没有找到，使用默认仓库
            if (!selectedWarehouse) {
                selectedWarehouse = warehouses.find(w => w.is_current) || warehouses[0];
                currentWarehouseId = selectedWarehouse.id;
            }
            
            // 更新显示
            currentWarehouseEl.textContent = selectedWarehouse.name;
            
            // 填充仓库列表
            warehouseList.innerHTML = '';
            warehouses.forEach(warehouse => {
                const item = document.createElement('a');
                item.href = '#';
                item.className = `block px-4 py-2 text-sm ${warehouse.id === currentWarehouseId ? 'bg-primary text-white' : 'text-gray-700 hover:bg-gray-100'}`;
                item.textContent = warehouse.name;
                item.dataset.warehouseId = warehouse.id;
                item.dataset.warehouseName = warehouse.name;
                
                item.addEventListener('click', async (e) => {
                    e.preventDefault();
                    const warehouseId = parseInt(item.dataset.warehouseId);
                    const warehouseName = item.dataset.warehouseName;
                    
                    try {
                        await switchUserWarehouse(warehouseId);
                        
                        // 更新UI
                        currentWarehouseEl.textContent = warehouseName;
                        
                        // 更新选中状态
                        warehouseList.querySelectorAll('a').forEach(a => {
                            a.className = a.dataset.warehouseId == warehouseId ? 
                                'block px-4 py-2 text-sm bg-primary text-white' : 
                                'block px-4 py-2 text-sm text-gray-700 hover:bg-gray-100';
                        });
                        
                        // 触发仓库切换事件
                        window.dispatchEvent(new CustomEvent('warehouseChanged', { 
                            detail: { warehouseId, warehouseName } 
                        }));
                        
                    } catch (error) {
                        showError('切换仓库失败: ' + error.message);
                    }
                    
                    // 隐藏下拉菜单
                    warehouseDropdown.classList.add('hidden');
                });
                
                warehouseList.appendChild(item);
            });
            
            // 仓库选择器点击事件
            warehouseSelector.addEventListener('click', (e) => {
                e.stopPropagation();
                warehouseDropdown.classList.toggle('hidden');
                
                // 关闭其他下拉菜单
                document.querySelectorAll('.dropdown').forEach(dropdown => {
                    if (dropdown !== warehouseDropdown) {
                        dropdown.classList.add('hidden');
                    }
                });
            });
            
        } else {
            currentWarehouseEl.textContent = '无可用仓库';
            warehouseList.innerHTML = '<div class="px-4 py-2 text-sm text-gray-500">无可用仓库</div>';
        }
        
    } catch (error) {
        console.error('初始化仓库选择器失败:', error);
        currentWarehouseEl.textContent = '加载失败';
    }
}

// 完整的页面初始化
async function initPage() {
    if (!(await initAuthenticatedPage())) {
        return;
    }
    
    initSidebar();
    initUserMenu();
    await initWarehouseSelector();
}

// 导出函数（如果使用模块）
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        checkAuth,
        getAuthHeaders,
        getHeaders,
        logout,
        apiGet,
        apiPost,
        apiPut,
        apiDelete,
        loadUserWarehouses,
        switchUserWarehouse,
        getCurrentWarehouse,
        showLoading,
        showMessage,
        showSuccess,
        showError,
        showWarning,
        showConfirm,
        formatDateTime,
        formatNumber,
        initAuthenticatedPage,
        initUserInfo,
        initSidebar,
        initUserMenu,
        initWarehouseSelector,
        initPage
    };
}
