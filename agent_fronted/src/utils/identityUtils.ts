// identityUtils.ts
// 身份管理工具函数

import { useUserIdentityStore } from '@/stores/userIdentity';

/**
 * 处理用户身份信息的工具函数
 * @param userInfo - 用户信息对象
 * @returns 处理后的身份信息
 */
export const processUserInfo = (userInfo: any) => {
  // 获取用户身份存储
  const userIdentityStore = useUserIdentityStore();

  // 解析用户角色（支持多个角色）
  let roles: string[] = [];
  if (userInfo.user_role && typeof userInfo.user_role === 'string') {
    // 如果是字符串，按顿号分割
    roles = userInfo.user_role.split('、').map((role: string) => role.trim());
  } else if (Array.isArray(userInfo.user_role)) {
    // 如果是数组，直接使用
    roles = userInfo.user_role;
  }

  // 设置用户身份信息
  userIdentityStore.setUserInfo({
    userId: userInfo.user_id,
    userRole: roles.length > 0 ? roles.join(',') : null,
    displayName: userInfo.display_name || (roles.length > 0 ? roles[0] : null)
  });

  // 返回处理后的信息
  return {
    userId: userInfo.user_id,
    roles: roles,
    displayName: generateDisplayName(roles)
  };
};

/**
 * 生成显示名称
 * @param roles - 角色数组
 * @returns 显示名称
 */
export const generateDisplayName = (roles: string[]) => {
  if (!roles || roles.length === 0) {
    return '等待身份识别';
  }

  // 如果有多个角色，返回第一个角色
  const primaryRole = roles[0];

  // 根据角色生成显示名称
  if (primaryRole?.includes('系统工程师')) {
    return '系统工程师';
  } else if (primaryRole?.includes('数据分析师')) {
    return '数据分析师';
  } else if (primaryRole?.includes('技术专家')) {
    return '技术专家';
  } else if (primaryRole?.includes('总监')) {
    return '总监';
  } else {
    return primaryRole;
  }
};

/**
 * 持久化存储用户身份信息
 * @param userInfo - 用户信息
 */
// @ts-ignore - 保留以备将来使用
export const persistUserInfo = (userInfo: any) => {
/*  // 使用localStorage持久化存储
  try {
    localStorage.setItem('user_info', JSON.stringify(userInfo));
    console.log('用户身份信息已持久化存储');
  } catch (error) {
    console.error('持久化存储失败:', error);
  }*/
};

/**
 * 从持久化存储中获取用户身份信息
 * @returns 用户信息
 */
export const loadPersistedUserInfo = () => {
  /*try {
    const storedInfo = localStorage.getItem('user_info');
    if (storedInfo) {
      return JSON.parse(storedInfo);
    }
  } catch (error) {
    console.error('读取持久化存储失败:', error);
  }
  return null;*/
};

/**
 * 获取头像URL
 * @param roles - 角色数组
 * @returns 头像URL
 */
export const getAvatarUrl = (roles: string[]) => {
  if (!roles || roles.length === 0) {
    return '/src/assets/default-avatar.svg';
  }

  return '/src/assets/default-avatar.svg';
};
