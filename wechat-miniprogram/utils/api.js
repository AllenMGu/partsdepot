const { getToken, clearAuth } = require("./auth");

function request({ url, method = "GET", data = {}, headers = {}, withToken = true }) {
  const app = getApp();
  const token = getToken();
  const finalHeaders = Object.assign(
    {
      "content-type": "application/json"
    },
    headers
  );

  if (withToken && token) {
    finalHeaders.Authorization = `Bearer ${token}`;
  }

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${app.globalData.apiBaseUrl}${url}`,
      method,
      data,
      header: finalHeaders,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
          return;
        }

        if (res.statusCode === 401) {
          clearAuth();
          wx.reLaunch({ url: "/pages/login/index" });
        }

        const detail = (res.data && res.data.detail) || "请求失败";
        reject(new Error(detail));
      },
      fail(err) {
        reject(new Error(err.errMsg || "网络错误"));
      }
    });
  });
}

function login(username, password) {
  const app = getApp();
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${app.globalData.apiBaseUrl}/token`,
      method: "POST",
      data: { username, password },
      header: { "content-type": "application/x-www-form-urlencoded" },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          const detail = (res.data && res.data.detail) || "登录失败";
          reject(new Error(detail));
        }
      },
      fail(err) {
        reject(new Error(err.errMsg || "网络错误"));
      }
    });
  });
}

module.exports = {
  request,
  login
};
